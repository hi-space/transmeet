"""Exercise the real WebSocket contract with a controlled inference boundary."""
import importlib
import struct
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class FakeUtterance:
    def __init__(self, language, on_partial):
        self.language = language
        self.on_partial = on_partial
        self.audio = bytearray()
        self.finished = 0
        self.cancelled = False

    def feed(self, pcm):
        self.audio.extend(pcm)
        if len(self.audio) == len(pcm):
            self.on_partial("회의" if self.language == "ko-KR" else "Hello", self.language)

    async def finish(self):
        self.finished += 1
        return ("회의를 시작합니다." if self.language == "ko-KR" else "Hello there.", self.language)

    async def cancel(self):
        self.cancelled = True


class FakeRuntime:
    def __init__(self):
        self.utterances = []

    def start_utterance(self, *, language, chunk_ms, on_partial):
        assert chunk_ms in (320, 560)
        utterance = FakeUtterance(language, on_partial)
        self.utterances.append(utterance)
        return utterance


@pytest.fixture
def service():
    server = importlib.import_module("inference.nemotron_streaming.server")
    runtime = FakeRuntime()
    with TestClient(server.create_app(runtime)) as client:
        yield client, runtime


def start(ws, language="ko-KR", chunk_ms=560):
    ws.send_json({"type": "start", "language": language, "sample_rate": 16000, "chunk_ms": chunk_ms})
    assert ws.receive_json()["type"] == "ready"


def speech(value=5000):
    return struct.pack("<1600h", *([value] * 1600))


def test_partial_arrives_before_end_and_final_is_emitted_once(service):
    client, runtime = service
    with client.websocket_connect("/invocations-bidirectional-stream") as ws:
        start(ws)
        ws.send_bytes(speech())
        partial = ws.receive_json()
        assert partial == {"type": "partial", "utterance_id": 1, "text": "회의", "language": "ko-KR"}
        assert runtime.utterances[0].finished == 0
        ws.send_json({"type": "end"})
        assert ws.receive_json() == {
            "type": "final", "utterance_id": 1, "text": "회의를 시작합니다.", "language": "ko-KR",
        }
        assert ws.receive_json() == {"type": "ended"}
    assert runtime.utterances[0].finished == 1
    assert bytes(runtime.utterances[0].audio) == speech()


def test_flush_keeps_connection_and_creates_a_new_utterance_id(service):
    client, _ = service
    with client.websocket_connect("/invocations-bidirectional-stream") as ws:
        start(ws)
        for utterance_id in (1, 2):
            ws.send_bytes(speech())
            assert ws.receive_json()["utterance_id"] == utterance_id
            ws.send_json({"type": "flush"})
            final = ws.receive_json()
            assert final["type"] == "final" and final["utterance_id"] == utterance_id
        ws.send_json({"type": "end"})
        assert ws.receive_json() == {"type": "ended"}


def test_silence_and_empty_stop_never_open_an_inference_session(service):
    client, runtime = service
    with client.websocket_connect("/invocations-bidirectional-stream") as ws:
        start(ws)
        ws.send_bytes(b"\0" * 32000)
        ws.send_json({"type": "end"})
        assert ws.receive_json() == {"type": "ended"}
    assert runtime.utterances == []


@pytest.mark.parametrize("field,value", [
    ("language", "invalid"), ("sample_rate", 48000), ("chunk_ms", 300),
    ("type", "audio"),
])
def test_invalid_start_settings_produce_explicit_errors(service, field, value):
    client, runtime = service
    with client.websocket_connect("/invocations-bidirectional-stream") as ws:
        message = {"type": "start", "language": "ko-KR", "sample_rate": 16000, "chunk_ms": 560}
        message[field] = value
        ws.send_json(message)
        assert ws.receive_json()["type"] == "error"
    assert runtime.utterances == []


def test_simultaneous_recordings_have_independent_audio_and_language(service):
    client, runtime = service
    with client.websocket_connect("/invocations-bidirectional-stream") as ko:
        with client.websocket_connect("/invocations-bidirectional-stream") as en:
            start(ko, "ko-KR", 560)
            start(en, "en-US", 320)
            ko.send_bytes(speech(5000))
            en.send_bytes(speech(6000))
            assert ko.receive_json()["language"] == "ko-KR"
            assert en.receive_json()["language"] == "en-US"
            ko.send_json({"type": "end"})
            en.send_json({"type": "end"})
            assert ko.receive_json()["text"] == "회의를 시작합니다."
            assert en.receive_json()["text"] == "Hello there."
            assert ko.receive_json()["type"] == en.receive_json()["type"] == "ended"
    audio = {u.language: bytes(u.audio) for u in runtime.utterances}
    assert audio == {"ko-KR": speech(5000), "en-US": speech(6000)}


def test_invalid_pcm_and_large_frames_are_rejected(service):
    client, runtime = service
    for payload in (b"\x00", b"\0" * 64002):
        with client.websocket_connect("/invocations-bidirectional-stream") as ws:
            start(ws)
            ws.send_bytes(payload)
            assert ws.receive_json()["type"] == "error"
    assert runtime.utterances == []


def test_disconnect_cancels_an_unfinished_decoder(service):
    client, runtime = service
    with client.websocket_connect("/invocations-bidirectional-stream") as ws:
        start(ws)
        ws.send_bytes(speech())
        assert ws.receive_json()["type"] == "partial"
    assert runtime.utterances[0].cancelled
