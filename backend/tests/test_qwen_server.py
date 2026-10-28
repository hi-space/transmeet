"""The real ASGI transport, with inference as the controlled external boundary."""
import importlib
import struct
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class Utterance:
    def __init__(self, language, on_partial):
        self.language = "Korean" if language in ("ko-KR", "auto") else "English"
        self.on_partial = on_partial
        self.audio = bytearray()
        self.finished = 0
        self.cancelled = False

    def feed(self, pcm):
        self.audio.extend(pcm)
        self.on_partial("회의" if self.language == "Korean" else "Hello", self.language)

    async def finish(self):
        self.finished += 1
        return ("회의를 시작합니다." if self.language == "Korean" else "Hello there.", self.language)

    async def cancel(self):
        self.cancelled = True


class Runtime:
    def __init__(self):
        self.utterances = []

    def start_utterance(self, *, language, chunk_ms, on_partial):
        assert chunk_ms in (1000, 2000)
        utterance = Utterance(language, on_partial)
        self.utterances.append(utterance)
        return utterance


@pytest.fixture
def service():
    server = importlib.import_module("inference.qwen_streaming.server")
    runtime = Runtime()
    app = server.create_app(runtime)

    async def observed_app(scope, receive, send):
        try:
            await app(scope, receive, send)
        finally:
            completed = scope.get("_test_session_completed")
            if completed is not None:
                completed.set()

    with TestClient(observed_app) as client:
        connect = client.websocket_connect

        @contextmanager
        def completed_websocket(*args, **kwargs):
            session = connect(*args, **kwargs)
            completed = threading.Event()
            session.scope["_test_session_completed"] = completed
            with session as websocket:
                try:
                    yield websocket
                finally:
                    # Starlette 0.41 schedules ASGI cancellation on __exit__
                    # without waiting for a shared portal's handler cleanup.
                    websocket.close()
                    assert completed.wait(2), "WebSocket disconnect cleanup did not complete"

        client.websocket_connect = completed_websocket
        yield client, runtime


def start(ws, language="ko-KR", chunk_ms=1000):
    ws.send_json({"type": "start", "language": language, "sample_rate": 16000, "chunk_ms": chunk_ms})
    assert ws.receive_json() == {"type": "ready"}


def speech(samples=2560, value=5000):
    return struct.pack(f"<{samples}h", *([value] * samples))


def test_partial_precedes_final_and_short_tail_is_finalized_once(service):
    client, runtime = service
    with client.websocket_connect("/invocations-bidirectional-stream") as ws:
        start(ws)
        source = speech(2641)
        ws.send_bytes(source)
        assert ws.receive_json() == {
            "type": "partial", "utterance_id": 1, "text": "회의", "language": "Korean",
        }
        assert runtime.utterances[0].finished == 0
        ws.send_json({"type": "end"})
        assert ws.receive_json() == {
            "type": "final", "utterance_id": 1, "text": "회의를 시작합니다.", "language": "Korean",
        }
        assert ws.receive_json() == {"type": "ended"}
    assert runtime.utterances[0].finished == 1
    assert bytes(runtime.utterances[0].audio) == source


def test_flush_rotates_utterance_without_closing_connection(service):
    client, _ = service
    with client.websocket_connect("/invocations-bidirectional-stream") as ws:
        start(ws, chunk_ms=2000)
        for identity in (1, 2):
            ws.send_bytes(speech())
            assert ws.receive_json()["utterance_id"] == identity
            ws.send_json({"type": "flush"})
            result = ws.receive_json()
            assert result["type"] == "final" and result["utterance_id"] == identity
        ws.send_json({"type": "end"})
        assert ws.receive_json() == {"type": "ended"}


def test_idle_silence_creates_no_decoder(service):
    client, runtime = service
    with client.websocket_connect("/invocations-bidirectional-stream") as ws:
        start(ws)
        ws.send_bytes(b"\0" * 32000)
        ws.send_json({"type": "end"})
        assert ws.receive_json() == {"type": "ended"}
    assert runtime.utterances == []


@pytest.mark.parametrize("field,value", [
    ("language", "invalid"), ("sample_rate", 48000), ("chunk_ms", 560),
    ("chunk_ms", True), ("type", "audio"),
])
def test_invalid_start_is_explicit_error(service, field, value):
    client, runtime = service
    with client.websocket_connect("/invocations-bidirectional-stream") as ws:
        message = {"type": "start", "language": "ko-KR", "sample_rate": 16000, "chunk_ms": 1000}
        message[field] = value
        ws.send_json(message)
        assert ws.receive_json()["type"] == "error"
    assert runtime.utterances == []


def test_two_sessions_keep_language_audio_and_id_independent(service):
    client, runtime = service
    with client.websocket_connect("/invocations-bidirectional-stream") as ko:
        with client.websocket_connect("/invocations-bidirectional-stream") as en:
            start(ko, "ko-KR", 1000)
            start(en, "en-US", 2000)
            ko.send_bytes(speech(value=5000))
            en.send_bytes(speech(value=6000))
            assert ko.receive_json()["language"] == "Korean"
            assert en.receive_json()["language"] == "English"
            ko.send_json({"type": "end"})
            en.send_json({"type": "end"})
            assert ko.receive_json()["text"] == "회의를 시작합니다."
            assert en.receive_json()["text"] == "Hello there."
            assert ko.receive_json()["type"] == en.receive_json()["type"] == "ended"
    assert {u.language: bytes(u.audio) for u in runtime.utterances} == {
        "Korean": speech(value=5000), "English": speech(value=6000),
    }


def test_bad_pcm_and_large_frame_fail_instead_of_losing_audio(service):
    client, runtime = service
    for payload in (b"\0", b"\0" * 64002):
        with client.websocket_connect("/invocations-bidirectional-stream") as ws:
            start(ws)
            ws.send_bytes(payload)
            assert ws.receive_json()["type"] == "error"
    assert runtime.utterances == []


def test_disconnect_cancels_current_utterance(service):
    client, runtime = service
    with client.websocket_connect("/invocations-bidirectional-stream") as ws:
        start(ws)
        ws.send_bytes(speech())
        assert ws.receive_json()["type"] == "partial"
    assert runtime.utterances[0].cancelled
