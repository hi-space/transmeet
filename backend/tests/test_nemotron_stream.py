"""Real stream orchestration with a fake SageMaker event-stream boundary."""
import asyncio
import json
from contextlib import asynccontextmanager
from importlib import import_module

import pytest
from aws_sdk_sagemaker_runtime_http2.models import (
    ResponsePayloadPart,
    ResponseStreamEventPayloadPart,
)


class ModelConnection:
    def __init__(self, *, end_reply=True, fail=False, split=False, delayed_end=False):
        self.input_stream = self
        self.responses = asyncio.Queue()
        self.audio = []
        self.controls = []
        self.end_reply = end_reply
        self.fail = fail
        self.split = split
        self.delayed_end = delayed_end
        self.finish_task = None
        self.closed = False
        self.input_closed = False

    async def emit(self, event):
        encoded = json.dumps(event, ensure_ascii=False).encode()
        # Split inside a multibyte Korean character, not just at a JSON boundary.
        split_at = encoded.find("회".encode()) + 1 if self.split else -1
        if split_at > 0:
            self.responses.put_nowait(ResponseStreamEventPayloadPart(ResponsePayloadPart(
                bytes_=encoded[:split_at], data_type="UTF8", completion_state="PARTIAL",
            )))
            encoded = encoded[split_at:]
        self.responses.put_nowait(ResponseStreamEventPayloadPart(ResponsePayloadPart(
            bytes_=encoded, data_type="UTF8", completion_state="COMPLETE",
        )))

    async def send(self, event):
        part = event.value
        assert part.completion_state == "COMPLETE"
        if part.data_type == "BINARY":
            self.audio.append(part.bytes_)
            if self.fail:
                await self.emit({"type": "error", "message": "GPU decoder failed"})
            else:
                await self.emit({
                    "type": "partial", "utterance_id": 1,
                    "text": "회의를", "language": "ko-KR",
                })
        else:
            assert part.data_type == "UTF8"
            control = json.loads(part.bytes_)
            self.controls.append(control)
            if control["type"] == "start":
                await self.emit({"type": "ready"})
            elif control["type"] == "end" and self.end_reply:
                async def finish():
                    if self.delayed_end:
                        await asyncio.sleep(0.01)
                    if self.input_closed:
                        await self.emit({"type": "error", "message": "input closed before final flush"})
                        return
                    await self.emit({
                        "type": "final", "utterance_id": 1,
                        "text": "회의를 시작합니다.", "language": "ko-KR",
                    })
                    await self.emit({"type": "ended"})

                self.finish_task = asyncio.create_task(finish())

    async def close(self):
        self.input_closed = True

    async def await_output(self):
        async def events():
            while True:
                yield await self.responses.get()

        return None, events()


class ModelService:
    def __init__(self, **kwargs):
        self.connections = []
        self.kwargs = kwargs

    @asynccontextmanager
    async def connect(self, endpoint_name, region):
        assert endpoint_name == "stream-endpoint"
        assert region == "us-east-1"
        model = ModelConnection(**self.kwargs)
        self.connections.append(model)
        try:
            yield model
        finally:
            model.closed = True
            if model.finish_task:
                model.finish_task.cancel()
                await asyncio.gather(model.finish_task, return_exceptions=True)


def session(service, **kwargs):
    module = import_module("app.nemotron_stream")
    events = []

    async def on_event(event):
        events.append(event)

    stream = module.NemotronStream(
        endpoint_name="stream-endpoint", language="ko-KR", chunk_ms=560,
        region="us-east-1", connection_factory=service.connect, **kwargs,
    )
    stream.start(on_event)
    return stream, events


async def eventually(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


async def test_pcm_and_stop_retain_short_tail_and_decode_fragmented_hypotheses():
    service = ModelService(split=True)
    stream, events = session(service)
    pcm = b"\x12\x34" * 160
    await stream.send_audio(pcm)
    await eventually(lambda: any(event["type"] == "partial" for event in events))
    await stream.close()

    assert service.connections[0].audio == [pcm]
    assert service.connections[0].controls == [
        {"type": "start", "language": "ko-KR", "sample_rate": 16000, "chunk_ms": 560},
        {"type": "end"},
    ]
    assert [(event["type"], event.get("text")) for event in events] == [
        ("ready", None), ("partial", "회의를"),
        ("final", "회의를 시작합니다."), ("ended", None),
    ]
    assert stream.error is None
    assert stream.done
    assert service.connections[0].closed


async def test_stream_failure_is_reported_without_replaying_audio():
    service = ModelService(fail=True)
    stream, events = session(service)
    await stream.send_audio(b"\x12\x34")
    await eventually(lambda: stream.done)
    await stream.close()

    assert len(service.connections) == 1
    assert service.connections[0].audio == [b"\x12\x34"]
    assert service.connections[0].closed
    assert [event["message"] for event in events if event["type"] == "error"] == [
        "GPU decoder failed",
    ]
    with pytest.raises(Exception, match="GPU decoder failed"):
        await stream.send_audio(b"\x56\x78")


async def test_stop_timeout_releases_a_model_that_never_sends_ended():
    service = ModelService(end_reply=False)
    stream, events = session(service, close_timeout=0.025)
    await eventually(lambda: any(event["type"] == "ready" for event in events))
    await asyncio.wait_for(stream.close(), 0.2)
    assert service.connections[0].closed
    assert stream.done
    assert any(event["type"] == "error" for event in events)


async def test_end_waits_for_final_response_before_closing_the_sdk_input():
    service = ModelService(delayed_end=True)
    stream, events = session(service)
    await stream.send_audio(b"\x01\x02")
    await stream.close()
    assert stream.error is None
    assert [event["text"] for event in events if event["type"] == "final"] == [
        "회의를 시작합니다.",
    ]
    assert service.connections[0].input_closed


async def test_rotation_flushes_old_session_before_sending_queued_audio_to_new_one():
    service = ModelService()
    stream, events = session(service, rotate_seconds=0.025)
    await stream.send_audio(b"\x01\x02")
    await eventually(lambda: len(service.connections) == 2)
    await stream.send_audio(b"\x03\x04")
    await stream.close()

    assert len(service.connections) == 2
    assert [model.audio for model in service.connections] == [[b"\x01\x02"], [b"\x03\x04"]]
    assert all(model.closed for model in service.connections)
    finals = [event for event in events if event["type"] == "final"]
    assert len(finals) == 2
    assert finals[0]["stream_id"] != finals[1]["stream_id"]
    assert finals[0]["utterance_id"] == finals[1]["utterance_id"] == 1


async def test_backpressure_is_bounded_when_model_startup_stalls():
    entered, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def stalled(endpoint_name, region):
        entered.set()
        await release.wait()
        yield ModelConnection()

    service = ModelService()
    service.connect = stalled
    stream, events = session(service, queue_size=1, send_timeout=0.02, close_timeout=0.02)
    await entered.wait()
    await stream.send_audio(b"\x00\x00")
    with pytest.raises(Exception, match="queue|backpressure"):
        await asyncio.wait_for(stream.send_audio(b"\x01\x01"), 0.2)
    await asyncio.wait_for(stream.close(), 0.2)
    assert stream.done


@pytest.mark.parametrize("pcm", [b"\x00", b"x" * 512_002], ids=["unaligned", "too-large"])
async def test_malformed_or_oversized_pcm_is_rejected_before_model_input(pcm):
    stream, _ = session(ModelService())
    try:
        with pytest.raises(ValueError):
            await stream.send_audio(pcm)
    finally:
        await stream.close()


async def test_malformed_json_response_is_a_failure_not_silent_speech_loss():
    service = ModelService()
    stream, events = session(service)
    await eventually(lambda: service.connections)
    service.connections[0].responses.put_nowait(ResponseStreamEventPayloadPart(
        ResponsePayloadPart(bytes_=b"not JSON", data_type="UTF8", completion_state="COMPLETE"),
    ))
    await eventually(lambda: stream.done)
    assert stream.error is not None
    assert service.connections[0].closed
    assert any(event["type"] == "error" for event in events)


async def test_credentials_use_ecs_role_and_refresh_between_connections(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    issued = []

    class CredentialsHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            issued.append(len(issued) + 1)
            body = json.dumps({
                "AccessKeyId": "test-access-key",
                "SecretAccessKey": "test-secret",
                "Token": f"test-ecs-session-{issued[-1]}",
                "Expiration": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), CredentialsHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for name in (
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-credentials"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-config"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", f"http://127.0.0.1:{server.server_port}/")
    try:
        module = import_module("app.nemotron_stream")
        resolver = module.CredentialChainResolver("us-east-1")
        first = await resolver.get_identity(properties={})
        second = await resolver.get_identity(properties={})
        assert first.access_key_id == second.access_key_id == "test-access-key"
        assert first.session_token == "test-ecs-session-1"
        assert second.session_token == "test-ecs-session-2"
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join()


@pytest.mark.parametrize("startup_fails", [False, True])
async def test_sdk_factory_uses_http2_without_retries_and_closes_transport_on_exit(
    monkeypatch, startup_fails,
):
    module = import_module("app.nemotron_stream")
    transports = []
    requests = []
    model = ModelConnection()
    credential_reads = []

    async def identity(self, *, properties):
        credential_reads.append(self.region)
        return module.AWSCredentialsIdentity(
            access_key_id="test-access-key", secret_access_key="test-secret",
            session_token="test-ecs-session",
        )

    class Transport:
        def __init__(self, *, client_config):
            assert client_config.force_http_2
            self.client_config = client_config
            self.closed = False
            transports.append(self)

        def __deepcopy__(self, memo):
            return Transport(client_config=self.client_config)

        async def close(self):
            self.closed = True

    # Exercise the installed SDK's real setup/config-copy/close lifecycle; only
    # invocation is replaced so no SageMaker endpoint or credentials are needed.
    class Client(module.AsyncSageMakerRuntimeHTTP2Client):
        async def invoke_endpoint_with_bidirectional_stream(self, input):
            settings = await self._ensure_setup()
            requests.append((input.endpoint_name, settings.region, settings.max_attempts))
            assert settings.endpoint_uri == "https://runtime.sagemaker.us-east-1.amazonaws.com:8443"
            credentials = await settings.aws_credentials_identity_resolver.get_identity(properties={})
            assert credentials.session_token == "test-ecs-session"
            if startup_fails:
                raise ConnectionError("connection refused")
            return model

    monkeypatch.setattr(module, "AWSCRTHTTPClient", Transport)
    monkeypatch.setattr(module.CredentialChainResolver, "get_identity", identity)
    from types import SimpleNamespace
    monkeypatch.setattr(module, "AWSCRTHTTPClientConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(module, "AsyncSageMakerRuntimeHTTP2Client", Client)
    if startup_fails:
        with pytest.raises(ConnectionError, match="connection refused"):
            async with module.open_sdk_stream("stream-endpoint", "us-east-1"):
                pass
    else:
        async with module.open_sdk_stream("stream-endpoint", "us-east-1") as stream:
            assert stream is model
        assert model.input_closed
    assert requests == [("stream-endpoint", "us-east-1", 1)]
    assert credential_reads == ["us-east-1"]
    assert transports
    assert all(transport.closed for transport in transports)


async def test_isolated_worker_forwards_pcm_and_split_responses_and_bounds_stderr(monkeypatch, tmp_path):
    import sys
    module = import_module("app.nemotron_stream")
    worker = tmp_path / "model_worker.py"
    worker.write_text(r'''
import json, struct, sys, time
reader, writer = sys.stdin.buffer, sys.stdout.buffer
def emit(kind, payload):
    writer.write(kind + struct.pack(">I", len(payload)) + payload)
    writer.flush()
sys.stderr.write("x" * 100000)
sys.stderr.flush()
audio = bytearray()
while True:
    header = reader.read(5)
    if not header:
        break
    kind, size = header[:1], struct.unpack(">I", header[1:])[0]
    payload = reader.read(size)
    if kind == b"B":
        audio.extend(payload)
    elif kind == b"J":
        message = json.loads(payload)
        if message["type"] == "start":
            assert message == {"type":"start","language":"ko-KR","sample_rate":16000,"chunk_ms":560}
            emit(b"R", b'{"type":"ready"}')
        elif message["type"] == "end":
            assert audio == b"\x01\x02\x03\x04"
            time.sleep(0.01)
            final = json.dumps({"type":"final","utterance_id":1,"text":"회의","language":"ko-KR"}, ensure_ascii=False).encode()
            index = final.index("회".encode()) + 1
            emit(b"P", final[:index])
            emit(b"R", final[index:])
            emit(b"R", b'{"type":"ended"}')
    elif kind == b"C":
        break
''')
    spawn = asyncio.create_subprocess_exec
    processes = []

    async def launch(*args, **kwargs):
        assert args[0] == "/isolated/python"
        assert "--worker" in args
        process = await spawn(sys.executable, str(worker), **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", launch)
    events = []

    async def on_event(event):
        events.append(event)

    stream = module.NemotronStream(
        "stream-endpoint", language="ko-KR", worker_python="/isolated/python",
    )
    stream.start(on_event)
    await stream.send_audio(b"\x01\x02\x03\x04")
    await stream.close()
    assert stream.error is None
    assert [event["text"] for event in events if event["type"] == "final"] == ["회의"]
    assert len(processes) == 1
    assert processes[0].returncode == 0


async def test_unresponsive_worker_is_killed_when_graceful_close_times_out(monkeypatch, tmp_path):
    import sys
    module = import_module("app.nemotron_stream")
    worker = tmp_path / "stalled_worker.py"
    worker.write_text("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n")
    spawn = asyncio.create_subprocess_exec
    processes = []

    async def launch(*args, **kwargs):
        process = await spawn(sys.executable, str(worker), **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", launch)
    stream = module.NemotronStream(
        "stream-endpoint", worker_python="/isolated/python", close_timeout=0.02,
    )
    events = []

    async def on_event(event):
        events.append(event)

    stream.start(on_event)
    await eventually(lambda: processes)
    await asyncio.wait_for(stream.close(), 5)
    assert stream.done
    assert stream.error is not None
    assert processes[0].returncode is not None


async def test_worker_exit_flushes_all_frames_when_stdout_is_backpressured():
    import struct
    import sys
    child = r'''
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from app import nemotron_stream as m
from aws_sdk_sagemaker_runtime_http2.models import ResponsePayloadPart, ResponseStreamEventPayloadPart
async def ignore(*args): pass
class Connection:
    input_stream = SimpleNamespace(send=ignore, close=ignore)
    async def await_output(self):
        async def output():
            for n in range(160):
                yield ResponseStreamEventPayloadPart(ResponsePayloadPart(
                    bytes_=b"x" * 4096, data_type="UTF8", completion_state="COMPLETE"))
        return None, output()
@asynccontextmanager
async def connect(*args):
    yield Connection()
m.open_sdk_stream = connect
asyncio.run(m._worker_main("fake-endpoint", "us-east-1"))
'''
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", child, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=4096,
    )
    output = bytearray()
    try:
        async with asyncio.timeout(5):
            # Fill stdout's pipe and writer buffers before consuming slowly.
            await asyncio.sleep(0.2)
            while data := await process.stdout.read(4096):
                output.extend(data)
                await asyncio.sleep(0.001)
            await process.wait()
        assert process.returncode == 0, (await process.stderr.read()).decode()
        assert output == (b"R" + struct.pack(">I", 4096) + b"x" * 4096) * 160
    finally:
        process.stdin.close()
        if process.returncode is None:
            process.kill()
            await process.wait()
