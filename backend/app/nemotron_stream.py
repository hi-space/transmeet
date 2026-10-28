"""One bounded Nemotron recording over SageMaker's bidirectional HTTP/2 API.

Audio is never retried: a transport failure leaves an explicit error. Planned
rotation first flushes the old decoder, then consumes the remaining FIFO audio
with a fresh connection before SageMaker's 30-minute connection limit.
"""
import asyncio
import json
import logging
import os
import struct
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Awaitable, Callable

import boto3
from aws_sdk_sagemaker_runtime_http2.client import AsyncSageMakerRuntimeHTTP2Client
from aws_sdk_sagemaker_runtime_http2.config import AsyncSageMakerRuntimeHTTP2Config
from aws_sdk_sagemaker_runtime_http2.models import (
    InvokeEndpointWithBidirectionalStreamInput,
    RequestPayloadPart,
    RequestStreamEventPayloadPart,
    ResponsePayloadPart,
    ResponseStreamEventPayloadPart,
)
from smithy_aws_core.identity import AWSCredentialsIdentity
from smithy_aws_core.identity.static import StaticCredentialsResolver
from smithy_http.aio.crt import AWSCRTHTTPClient, AWSCRTHTTPClientConfig
from smithy_http.interfaces import HTTPRequestConfiguration

logger = logging.getLogger(__name__)

PCM_FRAME_BYTES = 5_120  # 160 ms; 64 queued frames hold at most 10.24 s of audio.
MAX_INPUT_BYTES = 512_000
MAX_RESPONSE_BYTES = 65_536
_END = object()
_ROTATE = object()
EventHandler = Callable[[dict], Awaitable[None]]


class NemotronStreamError(RuntimeError):
    pass


class CredentialChainResolver:
    """Resolve the AWS chain, including ECS/IMDS/SSO and temporary tokens.

    SDK 0.12.0's installed Smithy provider chain does not register an ECS
    provider. Freeze fresh botocore credentials for each signing call, off the
    event loop; no credential object is held across rotated connections.
    """
    def __init__(self, region: str):
        self.region = region

    async def get_identity(self, *, properties):
        def resolve():
            credentials = boto3.Session(region_name=self.region).get_credentials()
            if credentials is None:
                raise NemotronStreamError("AWS credentials are unavailable")
            frozen = credentials.get_frozen_credentials()
            return AWSCredentialsIdentity(
                access_key_id=frozen.access_key,
                secret_access_key=frozen.secret_key,
                session_token=frozen.token,
            )

        return await asyncio.to_thread(resolve)


@asynccontextmanager
async def open_sdk_stream(endpoint_name: str, region: str):
    """Called only in the isolated SDK worker (modern CRT), never the app process."""
    transport = AWSCRTHTTPClient(client_config=AWSCRTHTTPClientConfig(force_http_2=True))
    client = None
    stream = None
    try:
        # Resolve before invoking: SDK 0.12.0 can leave its request future pending
        # when credential resolution fails during signing. These frozen credentials
        # belong only to this connection; rotation resolves the provider chain again.
        identity = await asyncio.wait_for(
            CredentialChainResolver(region).get_identity(properties={}), timeout=10,
        )
        settings = await AsyncSageMakerRuntimeHTTP2Config.resolve(
            region=region,
            endpoint_uri=f"https://runtime.sagemaker.{region}.amazonaws.com:8443",
            transport=transport,
            aws_credentials_identity_resolver=StaticCredentialsResolver(identity),
            max_attempts=1,
            http_request_config=HTTPRequestConfiguration(read_timeout=30),
        )
        client = AsyncSageMakerRuntimeHTTP2Client(config=settings)
        stream = await asyncio.wait_for(
            client.invoke_endpoint_with_bidirectional_stream(
                InvokeEndpointWithBidirectionalStreamInput(endpoint_name=endpoint_name),
            ), timeout=30,
        )
        yield stream
    finally:
        # Config copies share the transport. Explicitly close it as well as the
        # client so startup failures before SDK ownership is established are safe.
        for resource in (stream, client, transport):
            if resource is not None:
                try:
                    await asyncio.wait_for(resource.close(), timeout=2)
                except (Exception, asyncio.CancelledError):
                    logger.debug("Nemotron transport cleanup failed", exc_info=True)


async def _read_frame(reader):
    try:
        header = await reader.readexactly(5)
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            return None
        raise NemotronStreamError("Truncated Nemotron worker frame") from exc
    size = struct.unpack(">I", header[1:])[0]
    if size > MAX_INPUT_BYTES + 4096:
        raise NemotronStreamError("Nemotron worker frame exceeded the limit")
    try:
        return header[:1], await reader.readexactly(size)
    except asyncio.IncompleteReadError as exc:
        raise NemotronStreamError("Truncated Nemotron worker payload") from exc


async def _write_frame(writer, kind, payload):
    writer.write(kind + struct.pack(">I", len(payload)) + payload)
    await writer.drain()


class _WorkerInput:
    def __init__(self, writer):
        self.writer = writer
        self.closed = False

    async def send(self, event):
        if self.closed:
            raise NemotronStreamError("Nemotron worker input is closed")
        part = event.value
        if part.completion_state != "COMPLETE" or part.data_type not in ("UTF8", "BINARY"):
            raise NemotronStreamError("Invalid Nemotron worker input")
        await _write_frame(
            self.writer, b"B" if part.data_type == "BINARY" else b"J", part.bytes_ or b"",
        )

    async def close(self):
        if not self.closed:
            self.closed = True
            try:
                await _write_frame(self.writer, b"C", b"")
            except (BrokenPipeError, ConnectionResetError):
                pass  # The server may close its connection after sending ended.


class _WorkerStream:
    def __init__(self, process):
        self.process = process
        self.input_stream = _WorkerInput(process.stdin)
        self._stderr = bytearray()
        self._stderr_task = asyncio.create_task(self._capture_stderr())
        self._closed = False

    async def _capture_stderr(self):
        while data := await self.process.stderr.read(4096):
            self._stderr.extend(data)
            del self._stderr[:-8192]

    async def await_output(self):
        async def events():
            while (frame := await _read_frame(self.process.stdout)) is not None:
                kind, payload = frame
                if kind == b"E":
                    raise NemotronStreamError(payload.decode("utf-8", errors="replace"))
                if kind not in (b"P", b"R"):
                    raise NemotronStreamError("Invalid Nemotron worker response")
                yield ResponseStreamEventPayloadPart(ResponsePayloadPart(
                    bytes_=payload, data_type="UTF8",
                    completion_state="PARTIAL" if kind == b"P" else "COMPLETE",
                ))
            if self.process.returncode not in (None, 0):
                detail = self._stderr.decode("utf-8", errors="replace").strip()
                raise NemotronStreamError(f"Nemotron worker exited: {detail[-2000:]}")

        return None, events()

    async def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            await asyncio.wait_for(self.input_stream.close(), timeout=0.5)
        except Exception:
            pass
        self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=1.5)
        except TimeoutError:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), timeout=0.5)
            except TimeoutError:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
                await self.process.wait()
        finally:
            self._stderr_task.cancel()
            await asyncio.gather(self._stderr_task, return_exceptions=True)


@asynccontextmanager
async def open_sagemaker_stream(endpoint_name: str, region: str, worker_python=None):
    """A worker isolates CRT 0.32 from amazon-transcribe's pinned CRT 0.26.

    Length-prefixed pipes carry raw PCM and bounded response fragments. Stdout
    is reserved for this protocol; stderr is continuously drained and bounded.
    """
    python = worker_python or os.environ.get("NEMOTRON_STREAM_PYTHON") or "/opt/nemotron-stream/bin/python"
    process = await asyncio.create_subprocess_exec(
        python, str(Path(__file__).resolve()), "--worker", endpoint_name, region,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=MAX_INPUT_BYTES + 4096,
    )
    stream = _WorkerStream(process)
    try:
        yield stream
    finally:
        await stream.close()


class _WorkerOutputProtocol(asyncio.streams.FlowControlMixin):
    def __init__(self, loop):
        super().__init__(loop=loop)
        self.closed = loop.create_future()

    def connection_lost(self, exc):
        super().connection_lost(exc)
        if not self.closed.done():
            if exc is None:
                self.closed.set_result(None)
            else:
                self.closed.set_exception(exc)

    def _get_close_waiter(self, stream):
        return self.closed


async def _worker_main(endpoint_name: str, region: str):
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=MAX_INPUT_BYTES + 4096)
    read_transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer,
    )
    write_transport, protocol = await loop.connect_write_pipe(
        lambda: _WorkerOutputProtocol(loop), sys.stdout.buffer,
    )
    writer = asyncio.StreamWriter(write_transport, protocol, None, loop)
    try:
        async with open_sdk_stream(endpoint_name, region) as connection:
            async def send():
                while (frame := await _read_frame(reader)) is not None:
                    kind, payload = frame
                    if kind == b"C":
                        await connection.input_stream.close()
                        return
                    if kind not in (b"B", b"J"):
                        raise NemotronStreamError("Invalid Nemotron worker request")
                    await connection.input_stream.send(RequestStreamEventPayloadPart(RequestPayloadPart(
                        bytes_=payload, data_type="BINARY" if kind == b"B" else "UTF8",
                        completion_state="COMPLETE",
                    )))

            async def receive():
                _, output = await connection.await_output()
                async for event in output:
                    if not isinstance(event, ResponseStreamEventPayloadPart):
                        value = getattr(event, "value", None)
                        raise NemotronStreamError(getattr(value, "message", None) or "SageMaker stream failed")
                    part = event.value
                    if (part.data_type not in (None, "UTF8")
                            or part.completion_state not in ("PARTIAL", "COMPLETE")):
                        raise NemotronStreamError("Invalid SageMaker response framing")
                    payload = part.bytes_ or b""
                    if len(payload) > MAX_RESPONSE_BYTES:
                        raise NemotronStreamError("SageMaker response exceeded the message limit")
                    await _write_frame(
                        writer, b"P" if part.completion_state == "PARTIAL" else b"R", payload,
                    )

            sender = asyncio.create_task(send())
            receiver = asyncio.create_task(receive())
            try:
                done, _ = await asyncio.wait((sender, receiver), return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    await task
            finally:
                sender.cancel()
                receiver.cancel()
                await asyncio.gather(sender, receiver, return_exceptions=True)
    except Exception as exc:
        await _write_frame(writer, b"E", str(exc).encode()[:MAX_RESPONSE_BYTES])
    finally:
        read_transport.close()
        writer.close()
        try:
            # drain() only reaches the low watermark. Wait for every final byte
            # and connection_lost before asyncio.run shuts down this event loop.
            await asyncio.wait_for(writer.wait_closed(), timeout=2)
        except TimeoutError as exc:
            write_transport.abort()
            raise NemotronStreamError("Nemotron worker output flush timed out") from exc
        except (BrokenPipeError, ConnectionResetError):
            pass  # Parent disconnected; its bounded cleanup releases this worker.


async def response_messages(output_stream):
    """Reassemble transport fragments before decoding UTF-8 or parsing JSON."""
    pending = bytearray()
    async for event in output_stream:
        if not isinstance(event, ResponseStreamEventPayloadPart):
            value = getattr(event, "value", None)
            raise NemotronStreamError(getattr(value, "message", None) or "SageMaker stream failed")
        part = event.value
        if part.data_type not in (None, "UTF8"):
            raise NemotronStreamError("Nemotron returned a non-JSON response")
        if part.completion_state not in ("PARTIAL", "COMPLETE"):
            raise NemotronStreamError("Nemotron response has an invalid completion state")
        pending.extend(part.bytes_ or b"")
        if len(pending) > MAX_RESPONSE_BYTES:
            raise NemotronStreamError("Nemotron response exceeded the message limit")
        if part.completion_state == "PARTIAL":
            continue
        try:
            message = json.loads(pending)
        except (UnicodeDecodeError, ValueError) as exc:
            raise NemotronStreamError("Nemotron returned malformed JSON") from exc
        pending.clear()
        if not isinstance(message, dict) or message.get("type") not in (
            "ready", "partial", "final", "ended", "error",
        ):
            raise NemotronStreamError("Nemotron returned an invalid event")
        if message["type"] in ("partial", "final"):
            utterance = message.get("utterance_id")
            if (not isinstance(utterance, int) or isinstance(utterance, bool) or utterance < 0
                    or not isinstance(message.get("text"), str)
                    or not isinstance(message.get("language", ""), str)):
                raise NemotronStreamError("Nemotron returned an invalid transcript")
        yield message
    if pending:
        raise NemotronStreamError("Nemotron response ended mid-message")


class NemotronStream:
    def __init__(
        self, endpoint_name: str, language: str = "auto", chunk_ms: int = 560,
        region: str = "us-east-1", *, connection_factory=None, queue_size: int = 64,
        send_timeout: float = 1, close_timeout: float = 12,
        startup_timeout: float = 30, rotate_seconds: float = 25 * 60,
        worker_python: str | None = None,
    ):
        if language not in ("ko-KR", "en-US", "auto") or chunk_ms not in (320, 560):
            raise ValueError("Invalid Nemotron language or chunk size")
        if not endpoint_name or queue_size < 1 or not 0 < rotate_seconds <= 25 * 60:
            raise ValueError("Invalid Nemotron stream configuration")
        self.endpoint_name = endpoint_name
        self.language = language
        self.chunk_ms = chunk_ms
        self.region = region
        self.connection_factory = connection_factory or (
            lambda endpoint, aws_region: open_sagemaker_stream(endpoint, aws_region, worker_python)
        )
        self.send_timeout = send_timeout
        self.close_timeout = close_timeout
        self.startup_timeout = startup_timeout
        self.rotate_seconds = rotate_seconds
        self.error: Exception | None = None
        self._queue = asyncio.Queue(maxsize=queue_size)
        self._task: asyncio.Task | None = None
        self._closing = False
        self._on_event: EventHandler | None = None
        self._stream_id = 0

    @property
    def done(self):
        return self._task is not None and self._task.done()

    def start(self, on_event: EventHandler):
        if self._task is not None:
            raise RuntimeError("Nemotron stream is already started")
        self._on_event = on_event
        self._task = asyncio.create_task(self._run())

    async def send_audio(self, pcm: bytes):
        if len(pcm) % 2 or len(pcm) > MAX_INPUT_BYTES:
            raise ValueError("Nemotron requires bounded PCM16 audio messages")
        if self.error:
            raise self.error
        if self._closing or self.done or self._task is None:
            raise NemotronStreamError("Nemotron stream is closed")
        try:
            # One deadline covers the whole message, even with multiple frames.
            # Keep the browser's short final tail, without a minimum duration.
            async with asyncio.timeout(self.send_timeout):
                for offset in range(0, len(pcm), PCM_FRAME_BYTES):
                    if self.done:
                        raise self.error or NemotronStreamError("Nemotron stream is closed")
                    await self._queue.put(pcm[offset:offset + PCM_FRAME_BYTES])
        except TimeoutError as exc:
            raise NemotronStreamError("Nemotron audio queue is full (backpressure)") from exc

    async def close(self):
        if self._task is None or self.done:
            return
        try:
            async with asyncio.timeout(self.close_timeout):
                if not self._closing:
                    self._closing = True
                    await self._queue.put(_END)
                await asyncio.shield(self._task)
        except TimeoutError:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            await self._fail(NemotronStreamError("Nemotron final audio flush timed out"))

    async def _emit(self, message):
        await self._on_event({**message, "stream_id": self._stream_id})

    async def _fail(self, error):
        if self.error is None:
            self.error = error
            await self._emit({"type": "error", "message": str(error)})

    async def _run(self):
        try:
            while True:
                self._stream_id += 1
                async with AsyncExitStack() as stack:
                    connection = await asyncio.wait_for(
                        stack.enter_async_context(self.connection_factory(self.endpoint_name, self.region)),
                        timeout=self.startup_timeout,
                    )
                    reason = await self._exchange(connection)
                if reason is _END:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Nemotron stream failed: %s", exc)
            await self._fail(exc)
        finally:
            # Release byte strings when an error interrupts the sender.
            while not self._queue.empty():
                self._queue.get_nowait()

    async def _exchange(self, connection):
        ready = asyncio.Event()
        ending = False

        async def send_message(payload, data_type="UTF8"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            await asyncio.wait_for(connection.input_stream.send(
                RequestStreamEventPayloadPart(RequestPayloadPart(
                    bytes_=data, data_type=data_type, completion_state="COMPLETE",
                )),
            ), timeout=self.startup_timeout)

        async def send_audio():
            nonlocal ending
            await asyncio.wait_for(ready.wait(), timeout=self.startup_timeout)
            while True:
                item = await self._queue.get()
                if item is _END or item is _ROTATE:
                    ending = True
                    await send_message({"type": "end"})
                    return item
                await send_message(item, "BINARY")

        async def receive():
            _, output = await asyncio.wait_for(connection.await_output(), timeout=self.startup_timeout)
            async for message in response_messages(output):
                if message["type"] == "error":
                    raise NemotronStreamError(message.get("message") or "Nemotron model failed")
                if message["type"] == "ready":
                    ready.set()
                if message["type"] == "ended" and not ending:
                    raise NemotronStreamError("Nemotron stream ended before the audio was flushed")
                await self._emit(message)
                if message["type"] == "ended":
                    return
            raise NemotronStreamError("Nemotron stream disconnected before the final response")

        async def rotate():
            await asyncio.sleep(self.rotate_seconds)
            if not self._closing:
                # FIFO marker preserves accepted audio across connection rotation.
                await self._queue.put(_ROTATE)

        await send_message({
            "type": "start", "language": self.language, "sample_rate": 16000, "chunk_ms": self.chunk_ms,
        })
        sender = asyncio.create_task(send_audio())
        reader = asyncio.create_task(receive())
        rotation = asyncio.create_task(rotate())
        try:
            done, _ = await asyncio.wait((sender, reader), return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                await reader
                reason = await asyncio.wait_for(sender, timeout=self.close_timeout)
            else:
                reason = await sender
                await asyncio.wait_for(reader, timeout=self.close_timeout)
            # Keep both directions open until the decoder acknowledges its flush.
            await asyncio.wait_for(connection.input_stream.close(), timeout=self.close_timeout)
            return reason
        finally:
            for task in (sender, reader, rotation):
                task.cancel()
            await asyncio.gather(sender, reader, rotation, return_exceptions=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    if len(sys.argv) != 4 or sys.argv[1] != "--worker":
        raise SystemExit("Usage: nemotron_stream.py --worker ENDPOINT REGION")
    asyncio.run(_worker_main(sys.argv[2], sys.argv[3]))
