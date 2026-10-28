"""SageMaker bidirectional streaming and legacy Whisper batch protocols."""
import asyncio
import io
import json
import logging
import threading
import wave
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse

from .engine import UtteranceSegmenter

logger = logging.getLogger(__name__)
MAX_SESSIONS = 4
MAX_BATCH_REQUESTS = 4
MAX_AUDIO_BYTES = 64000
MAX_BATCH_REQUEST_BYTES = 6 * 1024 * 1024
FINAL_TIMEOUT = 20
BATCH_TIMEOUT = 55


def start_settings(message):
    if not isinstance(message, dict) or message.get("type") != "start":
        raise ValueError("The first message must be a start event")
    language = message.get("language", "auto")
    chunk_ms = message.get("chunk_ms", 1000)
    if language not in ("ko-KR", "en-US", "auto"):
        raise ValueError("language must be ko-KR, en-US, or auto")
    if message.get("sample_rate") != 16000:
        raise ValueError("Only 16000 Hz mono PCM16 audio is supported")
    if type(chunk_ms) is not int or chunk_ms not in (1000, 2000):
        raise ValueError("chunk_ms must be 1000 or 2000")
    return language, chunk_ms


def batch_audio(body, content_type):
    """Validate WAV metadata before any decoder can expand the payload."""
    language = "auto"
    if content_type == "application/json":
        payload = json.loads(body)
        if not isinstance(payload, dict) or not isinstance(payload.get("audio_input"), str):
            raise ValueError("JSON requests require audio_input containing hexadecimal WAV audio")
        languages = {"korean": "ko-KR", "english": "en-US", "auto": "auto", None: "auto"}
        if payload.get("language") not in languages:
            raise ValueError("Batch language must be korean, english, or auto")
        language = languages[payload.get("language")]
        if payload.get("task", "transcribe") != "transcribe":
            raise ValueError("Whisper supports task=transcribe; translation is a separate service")
        if set(payload) - {"audio_input", "language", "task"}:
            raise ValueError("Unsupported Whisper batch generation parameters")
        body = bytes.fromhex(payload["audio_input"])
    try:
        with wave.open(io.BytesIO(body), "rb") as sound:
            samples = sound.getnframes()
            rate = sound.getframerate()
            channels = sound.getnchannels()
            width = sound.getsampwidth()
            if not samples or not rate or samples > rate * 30:
                raise ValueError("WAV audio must contain more than zero and at most 30 seconds")
            raw = sound.readframes(samples)
            if len(raw) != samples * channels * width:
                raise ValueError("WAV audio is truncated")
    except (wave.Error, EOFError) as exc:
        raise ValueError("Invalid PCM WAV audio") from exc
    if (rate, channels, width) == (16000, 1, 2):
        return raw, language

    # Keep the deployed JumpStart handler's downmix/resampling behavior.
    import numpy as np
    from transformers.pipelines.audio_utils import ffmpeg_read

    audio = ffmpeg_read(body, 16000)
    if not len(audio) or len(audio) > 480000:
        raise ValueError("Decoded WAV audio must contain at most 30 seconds")
    if not np.isfinite(audio).all():
        raise ValueError("WAV audio contains invalid samples")
    pcm = (np.clip(audio, -1, 32767 / 32768) * 32768).astype("<i2").tobytes()
    return pcm, language


def create_app(runtime=None):
    state = SimpleNamespace(runtime=runtime, active=0, batch_active=0)

    @asynccontextmanager
    async def lifespan(app):
        # Uvicorn configures its own loggers, not application/root handlers.
        # Runtime speech-gate diagnostics must reach container stderr too.
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        if state.runtime is None:
            from .model_runtime import ModelRuntime
            state.runtime = await asyncio.to_thread(ModelRuntime.load)
        yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/ping")
    async def ping():
        return PlainTextResponse(
            "ready" if state.runtime else "loading",
            status_code=200 if state.runtime else 503,
        )

    @app.post("/invocations")
    async def invoke(request: Request):
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in ("audio/wav", "application/json"):
            return JSONResponse({"error": "Use audio/wav or application/json"}, status_code=415)
        if state.runtime is None or state.batch_active >= MAX_BATCH_REQUESTS:
            return JSONResponse({"error": "Whisper is at capacity; retry shortly"}, status_code=503)
        state.batch_active += 1
        try:
            async def read_body():
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_BATCH_REQUEST_BYTES:
                        raise OverflowError("Whisper batch request is too large")
                    body.extend(chunk)
                return bytes(body)

            body = await asyncio.wait_for(read_body(), timeout=15)
            audio, language = await asyncio.to_thread(batch_audio, body, content_type)
            text, detected = await asyncio.wait_for(
                state.runtime.transcribe(audio, language=language), timeout=BATCH_TIMEOUT,
            )
            return JSONResponse({"text": [text], "language": detected})
        except OverflowError as exc:
            return JSONResponse({"error": str(exc)}, status_code=413)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except asyncio.TimeoutError:
            return JSONResponse({"error": "Whisper batch transcription timed out"}, status_code=504)
        except RuntimeError as exc:
            status = 503 if "capacity" in str(exc) else 500
            logger.exception("Whisper batch inference failed")
            return JSONResponse({"error": str(exc)}, status_code=status)
        finally:
            state.batch_active -= 1

    @app.websocket("/invocations-bidirectional-stream")
    async def stream(ws: WebSocket):
        await ws.accept()
        if state.runtime is None or state.active >= MAX_SESSIONS:
            await ws.send_json({"type": "error", "message": "Whisper is at capacity; retry shortly"})
            await ws.close(code=1013)
            return
        state.active += 1
        incoming = asyncio.Queue(maxsize=32)
        outgoing = asyncio.Queue(maxsize=256)
        loop = asyncio.get_running_loop()
        owner_thread = threading.get_ident()
        current = None
        closed_partial = None
        utterance_id = 0
        reader = None
        writer = None

        async def read_events():
            while True:
                event = await asyncio.wait_for(ws.receive(), timeout=60)
                if event["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect(event.get("code", 1000))
                try:
                    incoming.put_nowait(event)
                except asyncio.QueueFull as exc:
                    raise RuntimeError("Whisper cannot keep up with incoming audio") from exc

        async def write_events():
            while True:
                event = await outgoing.get()
                try:
                    await asyncio.wait_for(ws.send_json(event), timeout=5)
                finally:
                    outgoing.task_done()

        async def guarded(operation, timeout, watch_decoder=False):
            """Observe disconnect/backpressure even while final inference runs."""
            work = asyncio.ensure_future(operation)
            watched = {work}
            for task in (reader, writer):
                if task is not None:
                    watched.add(task)
            decoder_task = getattr(current, "task", None) if watch_decoder else None
            if decoder_task is not None:
                watched.add(decoder_task)
            try:
                done, _ = await asyncio.wait(watched, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                for task in done - {work}:
                    task.result()
                    raise RuntimeError("Whisper stream worker ended unexpectedly")
                if work not in done:
                    raise asyncio.TimeoutError()
                return work.result()
            finally:
                if not work.done():
                    work.cancel()
                    await asyncio.gather(work, return_exceptions=True)

        def partial_callback(identity, closed):
            previous = [None]

            def emit(text, language):
                value = (text, language)
                if closed.is_set() or not text or value == previous[0]:
                    return
                previous[0] = value
                event = {"type": "partial", "utterance_id": identity, "text": text, "language": language}

                async def enqueue():
                    if not closed.is_set():
                        await outgoing.put(event)

                if threading.get_ident() == owner_thread:
                    try:
                        outgoing.put_nowait(event)
                    except asyncio.QueueFull as exc:
                        raise RuntimeError("Whisper client cannot keep up with partial results") from exc
                else:
                    future = asyncio.run_coroutine_threadsafe(enqueue(), loop)
                    try:
                        future.result(timeout=5)
                    except BaseException:
                        future.cancel()
                        raise
            return emit

        async def dispatch(events, language, chunk_ms):
            nonlocal current, utterance_id, closed_partial
            for kind, audio in events:
                if kind == "start":
                    utterance_id += 1
                    closed_partial = threading.Event()
                    current = state.runtime.start_utterance(
                        language=language, chunk_ms=chunk_ms,
                        on_partial=partial_callback(utterance_id, closed_partial),
                    )
                    current.feed(audio)
                elif kind == "audio":
                    current.feed(audio)
                elif kind == "end":
                    try:
                        text, detected = await guarded(current.finish(), FINAL_TIMEOUT)
                    except asyncio.TimeoutError as exc:
                        raise RuntimeError("Whisper final transcription timed out") from exc
                    closed_partial.set()
                    current = None
                    await guarded(outgoing.put({
                        "type": "final", "utterance_id": utterance_id,
                        "text": text, "language": detected,
                    }), timeout=5)

        try:
            raw_start = await asyncio.wait_for(ws.receive_text(), timeout=15)
            if len(raw_start) > 4096:
                raise ValueError("Start message is too large")
            language, chunk_ms = start_settings(json.loads(raw_start))
            segmenter = UtteranceSegmenter()
            reader = asyncio.create_task(read_events())
            writer = asyncio.create_task(write_events())
            await outgoing.put({"type": "ready"})
            while True:
                event = await guarded(incoming.get(), timeout=65, watch_decoder=True)
                audio = event.get("bytes")
                if audio is not None:
                    if len(audio) > MAX_AUDIO_BYTES or len(audio) % 2:
                        raise ValueError("Audio frames must be PCM16 and at most two seconds")
                    await dispatch(segmenter.feed(audio), language, chunk_ms)
                    continue
                raw = event.get("text") or ""
                if len(raw) > 4096:
                    raise ValueError("Control message is too large")
                control = json.loads(raw)
                if not isinstance(control, dict) or control.get("type") not in ("end", "flush"):
                    raise ValueError("Expected PCM audio, flush, or end")
                await dispatch(segmenter.finish(), language, chunk_ms)
                if control["type"] == "end":
                    await guarded(outgoing.put({"type": "ended"}), timeout=5)
                    await guarded(outgoing.join(), timeout=10)
                    await ws.close(code=1000)
                    break
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.exception("Whisper streaming session failed")
            error = {"type": "error", "message": str(exc) or "Whisper speech stream timed out"}
            try:
                if writer is not None and not writer.done():
                    await asyncio.wait_for(outgoing.put(error), timeout=2)
                    await asyncio.wait_for(outgoing.join(), timeout=3)
                else:
                    await asyncio.wait_for(ws.send_json(error), timeout=3)
                await ws.close(code=1011)
            except Exception:
                pass
        finally:
            if closed_partial is not None:
                closed_partial.set()
            try:
                if current is not None:
                    await current.cancel()
            finally:
                tasks = [task for task in (reader, writer) if task is not None]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                state.active -= 1

    return app


app = create_app()
