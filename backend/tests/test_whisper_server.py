"""The real Whisper ASGI protocol with inference as the external boundary."""
import asyncio
import importlib
import io
import struct
import sys
import threading
import wave
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def speech(samples=2560, value=5000):
    return struct.pack(f"<{samples}h", *([value] * samples))


def wav(audio=None, rate=16000, channels=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as sound:
        sound.setnchannels(channels)
        sound.setsampwidth(2)
        sound.setframerate(rate)
        sound.writeframes(speech() if audio is None else audio)
    return output.getvalue()


class Utterance:
    def __init__(self, language, on_partial):
        self.language = "English" if language == "en-US" else "Korean"
        self.on_partial = on_partial
        self.audio = bytearray()
        self.finished = 0
        self.cancelled = False
        self.block_finish = False

    def feed(self, pcm):
        self.audio.extend(pcm)
        self.on_partial("Hello" if self.language == "English" else "회의", self.language)

    async def finish(self):
        self.finished += 1
        if self.block_finish:
            await asyncio.Event().wait()
        return ("Hello there." if self.language == "English" else "회의를 시작합니다.", self.language)

    async def cancel(self):
        self.cancelled = True


class Runtime:
    def __init__(self):
        self.utterances = []
        self.batches = []

    def start_utterance(self, *, language, chunk_ms, on_partial):
        assert chunk_ms in (1000, 2000)
        utterance = Utterance(language, on_partial)
        self.utterances.append(utterance)
        return utterance

    async def transcribe(self, audio, *, language):
        self.batches.append((bytes(audio), language))
        name = "English" if language == "en-US" else "Korean"
        return ("Hello there." if name == "English" else "회의를 시작합니다.", name)


@pytest.fixture
def service():
    server = importlib.import_module("inference.whisper_streaming.server")
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


def test_ping_is_ready_after_runtime_load(service):
    client, _ = service
    response = client.get("/ping")
    assert response.status_code == 200 and response.text == "ready"


def test_partial_precedes_unique_final_and_stop_flushes_short_tail(service):
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


def test_flush_rotates_ids_and_discards_late_partials_from_finished_utterance(service):
    client, runtime = service
    with client.websocket_connect("/invocations-bidirectional-stream") as ws:
        start(ws, chunk_ms=2000)
        for identity in (1, 2):
            ws.send_bytes(speech())
            assert ws.receive_json()["utterance_id"] == identity
            ws.send_json({"type": "flush"})
            result = ws.receive_json()
            assert result["type"] == "final" and result["utterance_id"] == identity
            runtime.utterances[-1].on_partial("늦은 결과", "Korean")
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
def test_invalid_start_is_an_explicit_error(service, field, value):
    client, runtime = service
    with client.websocket_connect("/invocations-bidirectional-stream") as ws:
        message = {"type": "start", "language": "ko-KR", "sample_rate": 16000, "chunk_ms": 1000}
        message[field] = value
        ws.send_json(message)
        assert ws.receive_json()["type"] == "error"
    assert runtime.utterances == []


def test_sessions_have_independent_language_audio_and_identifiers(service):
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


def test_bad_pcm_or_large_frame_fails_before_audio_is_accepted(service):
    client, runtime = service
    for payload in (b"\0", b"\0" * 64002):
        with client.websocket_connect("/invocations-bidirectional-stream") as ws:
            start(ws)
            ws.send_bytes(payload)
            assert ws.receive_json()["type"] == "error"
    assert runtime.utterances == []


def test_disconnect_cancels_the_active_utterance(service):
    client, runtime = service
    with client.websocket_connect("/invocations-bidirectional-stream") as ws:
        start(ws)
        ws.send_bytes(speech())
        assert ws.receive_json()["type"] == "partial"
    assert runtime.utterances[0].cancelled


def test_final_timeout_is_explicit_and_cancels_the_utterance(service, monkeypatch):
    server = importlib.import_module("inference.whisper_streaming.server")
    monkeypatch.setattr(server, "FINAL_TIMEOUT", 0.02)
    client, runtime = service
    with client.websocket_connect("/invocations-bidirectional-stream") as ws:
        start(ws)
        ws.send_bytes(speech())
        assert ws.receive_json()["type"] == "partial"
        runtime.utterances[0].block_finish = True
        ws.send_json({"type": "end"})
        result = ws.receive_json()
        assert result["type"] == "error" and "timed out" in result["message"]
    assert runtime.utterances[0].cancelled


def test_session_capacity_is_explicit_and_released_on_disconnect(service, monkeypatch):
    server = importlib.import_module("inference.whisper_streaming.server")
    monkeypatch.setattr(server, "MAX_SESSIONS", 1)
    client, _ = service
    with client.websocket_connect("/invocations-bidirectional-stream") as first:
        start(first)
        with client.websocket_connect("/invocations-bidirectional-stream") as second:
            result = second.receive_json()
            assert result["type"] == "error" and "capacity" in result["message"]
    with client.websocket_connect("/invocations-bidirectional-stream") as third:
        start(third)
        third.send_json({"type": "end"})
        assert third.receive_json() == {"type": "ended"}


@pytest.mark.parametrize("language,expected", [("korean", "ko-KR"), ("english", "en-US")])
def test_json_batch_preserves_legacy_hex_wav_language_and_response_shape(service, language, expected):
    client, runtime = service
    response = client.post("/invocations", json={
        "audio_input": wav().hex(), "language": language, "task": "transcribe",
    })
    assert response.status_code == 200
    assert response.json() == {
        "text": ["Hello there." if expected == "en-US" else "회의를 시작합니다."],
        "language": "English" if expected == "en-US" else "Korean",
    }
    assert runtime.batches == [(speech(), expected)]


def test_wav_batch_normalizes_stereo_sample_rate_and_uses_auto_language(service):
    client, runtime = service
    audio = speech(4800 * 2, 6000)
    response = client.post(
        "/invocations", content=wav(audio, rate=48000, channels=2),
        headers={"content-type": "audio/wav"},
    )
    assert response.status_code == 200
    assert response.json() == {"text": ["회의를 시작합니다."], "language": "Korean"}
    normalized, language = runtime.batches[0]
    assert language == "auto" and len(normalized) == 3200
    values = struct.unpack("<1600h", normalized)
    # The existing ffmpeg helper uses an equal-power stereo downmix:
    # identical 6000-amplitude channels become 6000 * sqrt(2), not an average.
    assert max(abs(value - 8485) for value in values) <= 1


def test_batch_accepts_longer_than_stream_window_without_truncation(service):
    client, runtime = service
    source = speech(320000)
    response = client.post("/invocations", content=wav(source), headers={"content-type": "audio/wav"})
    assert response.status_code == 200
    assert runtime.batches == [(source, "auto")]


@pytest.mark.parametrize("payload,status", [
    ({"audio_input": "not hex"}, 400),
    ({"audio_input": "00"}, 400),
    ({"audio_input": "", "language": "invalid"}, 400),
    ({"audio_input": "", "task": "translate"}, 400),
    ([], 400),
])
def test_batch_rejects_invalid_audio_or_task_without_inference(service, payload, status):
    client, runtime = service
    response = client.post("/invocations", json=payload)
    assert response.status_code == status
    assert runtime.batches == []


def test_batch_rejects_overlong_and_truncated_wav_and_unsupported_content(service):
    client, runtime = service
    for body in (wav(speech(480001)), wav()[:-20]):
        response = client.post("/invocations", content=body, headers={"content-type": "audio/wav"})
        assert response.status_code == 400
    assert client.post("/invocations", content=b"bad", headers={"content-type": "audio/mp3"}).status_code == 415
    assert runtime.batches == []


def test_batch_enforces_request_size_while_reading(service, monkeypatch):
    server = importlib.import_module("inference.whisper_streaming.server")
    monkeypatch.setattr(server, "MAX_BATCH_REQUEST_BYTES", 100)
    client, runtime = service
    response = client.post("/invocations", content=wav(), headers={"content-type": "audio/wav"})
    assert response.status_code == 413
    assert runtime.batches == []


def test_batch_capacity_error_has_retryable_status(service):
    client, runtime = service

    async def overloaded(*args, **kwargs):
        raise RuntimeError("The Whisper speech decoder is at capacity; retry shortly")

    runtime.transcribe = overloaded
    response = client.post("/invocations", content=wav(), headers={"content-type": "audio/wav"})
    assert response.status_code == 503
    assert "capacity" in response.json()["error"]
