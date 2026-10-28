"""SageMaker provider contracts and buffered recording lifecycle."""
import asyncio
import base64
import io
import json
import wave
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import stt, ws_handler


def wav_audio():
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x20" * 8000)
    return out.getvalue()


def test_qwen_uses_jumpstart_audio_chat_contract():
    audio = wav_audio()
    request = stt.build_request("qwen3-asr", audio, "ko")
    payload = json.loads(request["Body"])
    assert request["ContentType"] == "application/json"
    assert payload["temperature"] == 0
    assert payload["max_tokens"] >= 256
    content = payload["messages"][0]["content"][0]
    assert content["type"] == "input_audio"
    assert content["input_audio"]["format"] == "wav"
    assert base64.b64decode(content["input_audio"]["data"]) == audio


@pytest.mark.parametrize("language,expected", [("ko", "ko-KR"), ("en", "en-US"), ("auto", "auto")])
def test_nemotron_conditions_the_model_on_selected_language(language, expected):
    audio = wav_audio()
    request = stt.build_request("nemotron", audio, language)
    payload = json.loads(request["Body"])
    assert request["ContentType"] == "application/json"
    assert payload["language"] == expected
    assert base64.b64decode(payload["audio_base64"]) == audio


def test_whisper_retains_existing_binary_contract():
    audio = wav_audio()
    request = stt.build_request("whisper", audio, "auto")
    assert request["ContentType"] == "audio/wav"
    assert request["Body"] == audio


@pytest.mark.parametrize(
    "provider,payload,text,language",
    [
        ("qwen3-asr", {"choices": [{"message": {"content": "language Korean<asr_text>회의를 시작하겠습니다."}, "finish_reason": "stop"}]}, "회의를 시작하겠습니다.", "Korean"),
        ("qwen3-asr", {"choices": [{"message": {"content": "language None<asr_text>"}, "finish_reason": "stop"}]}, "", ""),
        ("qwen3-asr", {"choices": [{"message": {"content": "Hello there."}, "finish_reason": "stop"}]}, "Hello there.", ""),
        ("nemotron", {"text": "회의를 시작합니다.<ko-KR>", "language": "auto"}, "회의를 시작합니다.", "ko-KR"),
        ("nemotron", {"text": "Let's begin. <en-US>", "language": "auto"}, "Let's begin.", "en-US"),
        ("nemotron", {"text": "회의입니다.", "language": "ko-KR"}, "회의입니다.", "ko-KR"),
        ("nemotron", {"text": "", "language": "auto"}, "", ""),
        ("whisper", {"text": ["회의를", "시작합니다."], "language": "ko"}, "회의를 시작합니다.", "ko"),
    ],
)
def test_responses_remove_metadata_without_changing_transcript(provider, payload, text, language):
    result = stt.parse_response(provider, payload)
    assert result.text == text
    assert result.language == language


@pytest.mark.parametrize(
    "provider,payload",
    [
        ("qwen3-asr", {"choices": []}),
        ("qwen3-asr", {"choices": [{"message": {"content": None}}]}),
        ("qwen3-asr", {"choices": [{"message": {"content": "cut off"}, "finish_reason": "length"}]}),
        ("nemotron", {"error": "model not loaded"}),
        ("whisper", {"text": {"unexpected": "shape"}}),
    ],
)
def test_bad_responses_do_not_look_like_successful_silence(provider, payload):
    with pytest.raises(ValueError):
        stt.parse_response(provider, payload)


def test_unknown_provider_is_not_routed_to_whisper():
    with pytest.raises(ValueError):
        stt.build_request("typo", wav_audio(), "ko")


async def test_missing_endpoint_fails_before_network(monkeypatch):
    monkeypatch.setattr(stt.config, "QWEN_ASR_ENDPOINT", "")
    with pytest.raises(ValueError, match="QWEN_ASR_ENDPOINT"):
        await stt.transcribe_audio("qwen3-asr", wav_audio(), "ko")


@pytest.mark.parametrize("component", ["", "test-nemotron-ic"])
async def test_invocation_uses_configured_endpoint_and_parses_result(monkeypatch, component):
    monkeypatch.setattr(stt.config, "NEMOTRON_ASR_ENDPOINT", "test-nemotron")
    monkeypatch.setattr(stt.config, "NEMOTRON_ASR_INFERENCE_COMPONENT", component)

    class Body:
        async def read(self):
            return json.dumps({"text": "안녕하세요.<ko-KR>", "language": "auto"}).encode()

    class Runtime:
        async def invoke_endpoint(self, **request):
            assert request["EndpointName"] == "test-nemotron"
            assert request.get("InferenceComponentName") == (component or None)
            assert json.loads(request["Body"])["language"] == "auto"
            return {"Body": Body()}

    @asynccontextmanager
    async def client():
        yield Runtime()

    monkeypatch.setattr(stt, "sagemaker_client", client)
    result = await stt.transcribe_audio("nemotron", wav_audio(), "auto")
    assert result.text == "안녕하세요."
    assert result.language == "ko-KR"


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, event):
        self.sent.append(event)


async def test_next_chunk_does_not_cancel_inference_and_stop_waits(monkeypatch):
    ws = FakeWS()
    state = ws_handler.ConnectionState(ws=ws, stt_provider="nemotron", source_lang="ko")
    state.audio_buffer.append(wav_audio())
    started, release = asyncio.Event(), asyncio.Event()
    completed = []

    async def transcribe(provider, audio, language):
        started.set()
        await release.wait()
        return stt.STTResult("회의를 시작합니다.", "ko-KR")

    async def process(ws, text, speaker, language, snapshot, timestamp):
        completed.append((text, snapshot.stt_provider, snapshot.source_lang))

    monkeypatch.setattr(ws_handler, "transcribe_audio", transcribe)
    monkeypatch.setattr(ws_handler, "_process_segment", process)
    monkeypatch.setattr(ws_handler, "_start_flush", lambda *args: None)
    monkeypatch.setattr(ws_handler, "FLUSH_DELAY_SECS", 0.001)
    await ws_handler._schedule_flush(state, ws, "test")
    await asyncio.wait_for(started.wait(), 1)
    # A second timer must not cancel the request already sent to SageMaker.
    await ws_handler._schedule_flush(state, ws, "test")
    stop = asyncio.create_task(ws_handler._stop_recording(state, ws, "test"))
    await asyncio.sleep(0.01)
    assert not stop.done()
    release.set()
    await asyncio.wait_for(stop, 1)
    assert completed == [("회의를 시작합니다.", "nemotron", "ko")]


async def test_utterance_keeps_provider_and_language_when_settings_change(monkeypatch):
    ws = FakeWS()
    state = ws_handler.ConnectionState(ws=ws, stt_provider="qwen3-asr", source_lang="ko")
    state.audio_buffer.append(wav_audio())
    started, release = asyncio.Event(), asyncio.Event()
    completed = []

    async def transcribe(provider, audio, language):
        assert (provider, language) == ("qwen3-asr", "ko")
        started.set()
        await release.wait()
        return stt.STTResult("새로운 안건입니다.", "Korean")

    async def process(ws, text, speaker, language, snapshot, timestamp):
        completed.append((snapshot.stt_provider, snapshot.source_lang, language))

    monkeypatch.setattr(ws_handler, "transcribe_audio", transcribe)
    monkeypatch.setattr(ws_handler, "_process_segment", process)
    pending = asyncio.create_task(ws_handler._flush_audio_buffer(state, ws, "test"))
    await asyncio.wait_for(started.wait(), 1)
    state.stt_provider = "whisper"
    state.source_lang = "en"
    release.set()
    await asyncio.wait_for(pending, 1)
    assert completed == [("qwen3-asr", "ko", "ko")]


def test_unconfigured_engine_does_not_send_audio_to_previous_engine(monkeypatch):
    calls = []

    async def authenticate(ws):
        return "user", ""

    async def transcribe(*args):
        calls.append(args)
        return stt.STTResult("Unexpected fallback.", "en")

    async def process(*args):
        pass

    monkeypatch.setattr(ws_handler, "_authenticate", authenticate)
    monkeypatch.setattr(ws_handler, "transcribe_audio", transcribe)
    monkeypatch.setattr(ws_handler, "_process_segment", process)
    monkeypatch.setattr(stt.config, "QWEN_ASR_ENDPOINT", "")
    app = FastAPI()
    app.include_router(ws_handler.router)
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"action": "startRecording", "sttProvider": "qwen3-asr"})
        assert "QWEN_ASR_ENDPOINT" in ws.receive_json()["message"]
        ws.send_json({"action": "sendAudio", "audioData": base64.b64encode(wav_audio()).decode()})
        ws.send_json({"action": "stopRecording"})
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["type"] == "pong"
    assert calls == []


async def test_pending_transcribe_final_keeps_old_meeting_and_language(monkeypatch):
    ws = FakeWS()
    state = ws_handler.ConnectionState(
        ws=ws, stt_provider="transcribe", source_lang="ko", target_lang="en", meeting_id="old",
    )
    state.seg_buffer.append(("마지막 발언입니다.", "speaker1", "ko"))
    release = asyncio.Event()
    completed = []

    async def process(ws, text, speaker, language, snapshot, timestamp):
        await release.wait()
        completed.append((snapshot.meeting_id, snapshot.source_lang, snapshot.target_lang))

    monkeypatch.setattr(ws_handler, "_process_segment", process)
    monkeypatch.setattr(ws_handler, "_start_flush", lambda *args: None)
    await ws_handler._stop_recording(state, ws, "test")
    state.meeting_id = "new"
    state.source_lang = "en"
    state.target_lang = "ko"
    release.set()
    await asyncio.gather(*list(state.segment_tasks))
    assert completed == [("old", "ko", "en")]


def test_recording_waits_for_managed_engine_capacity(monkeypatch):
    calls = []
    phase = {"status": "starting"}

    async def authenticate(ws):
        return "user", ""

    async def status(provider, **kwargs):
        return {"provider": provider, "status": phase["status"], "managed": True}

    async def transcribe(*args):
        calls.append(args[0])
        return stt.STTResult("안녕하세요.", "ko-KR")

    async def process(*args):
        pass

    async def publish():
        pass

    monkeypatch.setattr(ws_handler, "_authenticate", authenticate)
    monkeypatch.setattr(ws_handler.capacity, "status", status)
    monkeypatch.setattr(ws_handler.capacity, "publish_activity", publish)
    monkeypatch.setattr(ws_handler, "transcribe_audio", transcribe)
    monkeypatch.setattr(ws_handler, "_process_segment", process)
    monkeypatch.setattr(stt.config, "NEMOTRON_ASR_ENDPOINT", "test-nemotron")
    monkeypatch.setattr(stt.config, "NEMOTRON_ASR_INFERENCE_COMPONENT", "test-nemotron-ic")
    app = FastAPI()
    app.include_router(ws_handler.router)
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"action": "startRecording", "sttProvider": "nemotron", "sourceLang": "ko"})
        assert ws.receive_json()["status"] == "starting"
        ws.send_json({"action": "sendAudio", "audioData": base64.b64encode(wav_audio()).decode()})
        ws.send_json({"action": "stopRecording"})
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["type"] == "pong"
        assert calls == []
        phase["status"] = "ready"
        ws.send_json({"action": "startRecording", "sttProvider": "nemotron", "sourceLang": "ko"})
        ws.send_json({"action": "sendAudio", "audioData": base64.b64encode(wav_audio()).decode()})
        ws.send_json({"action": "stopRecording"})
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["type"] == "pong"
    assert calls == ["nemotron"]
