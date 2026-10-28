"""SageMaker bidirectional WebSocket protocol for Qwen meeting subtitles."""
import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

from .engine import UtteranceSegmenter

logger = logging.getLogger(__name__)
MAX_SESSIONS = 4
MAX_AUDIO_BYTES = 64000


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


def create_app(runtime=None):
    state = SimpleNamespace(runtime=runtime, active=0)

    @asynccontextmanager
    async def lifespan(app):
        if state.runtime is None:
            from .model_runtime import ModelRuntime
            state.runtime = await asyncio.to_thread(ModelRuntime.load)
        yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/ping")
    async def ping():
        return PlainTextResponse("ready" if state.runtime else "loading",
                                 status_code=200 if state.runtime else 503)

    @app.websocket("/invocations-bidirectional-stream")
    async def stream(ws: WebSocket):
        await ws.accept()
        if state.runtime is None or state.active >= MAX_SESSIONS:
            await ws.send_json({"type": "error", "message": "Qwen speech server is at capacity; retry shortly"})
            await ws.close(code=1013)
            return
        state.active += 1
        outgoing = asyncio.Queue(maxsize=256)
        loop = asyncio.get_running_loop()
        owner_thread = threading.get_ident()
        current = None
        utterance_id = 0
        writer = None

        async def write_events():
            while True:
                event = await outgoing.get()
                try:
                    await ws.send_json(event)
                finally:
                    outgoing.task_done()

        def partial_callback(identity):
            previous = [None]

            def emit(text, language):
                value = (text, language)
                if not text or value == previous[0]:
                    return
                previous[0] = value
                event = {"type": "partial", "utterance_id": identity,
                         "text": text, "language": language}
                if threading.get_ident() == owner_thread:
                    outgoing.put_nowait(event)
                else:
                    future = asyncio.run_coroutine_threadsafe(outgoing.put(event), loop)
                    try:
                        future.result(timeout=5)
                    except BaseException:
                        future.cancel()
                        raise
            return emit

        async def dispatch(events, language, chunk_ms):
            nonlocal current, utterance_id
            for kind, audio in events:
                if kind == "start":
                    utterance_id += 1
                    current = state.runtime.start_utterance(
                        language=language, chunk_ms=chunk_ms,
                        on_partial=partial_callback(utterance_id),
                    )
                    current.feed(audio)
                elif kind == "audio":
                    current.feed(audio)
                elif kind == "end":
                    text, detected = await asyncio.wait_for(current.finish(), timeout=20)
                    current = None
                    await outgoing.put({"type": "final", "utterance_id": utterance_id,
                                        "text": text, "language": detected})

        try:
            raw_start = await asyncio.wait_for(ws.receive_text(), timeout=15)
            if len(raw_start) > 4096:
                raise ValueError("Start message is too large")
            language, chunk_ms = start_settings(json.loads(raw_start))
            segmenter = UtteranceSegmenter()
            writer = asyncio.create_task(write_events())
            await outgoing.put({"type": "ready"})
            while True:
                event = await asyncio.wait_for(ws.receive(), timeout=60)
                if event["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect(event.get("code", 1000))
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
                    await outgoing.put({"type": "ended"})
                    await asyncio.wait_for(outgoing.join(), timeout=10)
                    await ws.close(code=1000)
                    break
        except WebSocketDisconnect:
            if current is not None:
                logger.warning("Qwen stream disconnected with an unfinished utterance")
        except Exception as exc:
            logger.exception("Qwen streaming session failed")
            error = {"type": "error", "message": str(exc) or "Qwen speech stream timed out"}
            try:
                if writer is not None and not writer.done():
                    await asyncio.wait_for(outgoing.put(error), timeout=2)
                    await asyncio.wait_for(outgoing.join(), timeout=3)
                else:
                    await ws.send_json(error)
                await ws.close(code=1011)
            except Exception:
                pass
        finally:
            if current is not None:
                await current.cancel()
            if writer is not None:
                writer.cancel()
                await asyncio.gather(writer, return_exceptions=True)
            state.active -= 1

    return app


app = create_app()
