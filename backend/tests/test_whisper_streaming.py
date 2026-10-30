"""Whisper streaming capability, recording lifetime, and legacy batch fallback."""
import asyncio
import base64
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from app import config as config_module, nemotron_stream, stt, stt_capacity, ws_handler
from app.stt_capacity import CapacityManager
from tests.test_nemotron_stream import ModelConnection, eventually
from tests.test_nemotron_ws import FakeWS, wav


@pytest.fixture
def whisper(monkeypatch):
    for name, value in {
        "REGION": "us-east-1",
        "WHISPER_ENDPOINT": "whisper-batch",
        "WHISPER_STREAMING_ENABLED": True,
        "WHISPER_STREAMING_ENDPOINT": "whisper-stream",
        "WHISPER_STREAMING_REGION": "us-west-2",
        "WHISPER_STREAMING_CHUNK_MS": 1000,
        "QWEN_ASR_INFERENCE_COMPONENT": "",
        "NEMOTRON_ASR_INFERENCE_COMPONENT": "",
    }.items():
        # The initial red run must reach missing behavior rather than fail setup.
        monkeypatch.setitem(stt.config.__dict__, name, value)


def test_whisper_environment_settings_enable_streaming_without_changing_batch(monkeypatch):
    for name, value in {
        "WHISPER_ENDPOINT": "existing-whisper",
        "WHISPER_STREAMING_ENABLED": "true",
        "WHISPER_STREAMING_ENDPOINT": "new-whisper",
        "WHISPER_STREAMING_REGION": "us-west-2",
        "WHISPER_STREAMING_CHUNK_MS": "2000",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(stt, "config", config_module.Config(_env_file=None))
    assert stt.is_streaming_provider("whisper")
    assert stt.active_endpoint_for("whisper") == "new-whisper"
    assert stt.active_region_for("whisper") == "us-west-2"
    assert stt.streaming_chunk_ms("whisper") == 2000
    assert stt.endpoint_for("whisper") == "existing-whisper"
    assert stt.active_inference_component_for("whisper") == ""


def test_whisper_defaults_keep_the_existing_batch_endpoint(monkeypatch):
    for name in (
        "WHISPER_STREAMING_ENABLED", "WHISPER_STREAMING_ENDPOINT",
        "WHISPER_STREAMING_REGION", "WHISPER_STREAMING_CHUNK_MS",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = config_module.Config(_env_file=None, WHISPER_ENDPOINT="existing-whisper")
    monkeypatch.setattr(stt, "config", settings)
    assert not stt.is_streaming_provider("whisper")
    assert stt.active_endpoint_for("whisper") == "existing-whisper"
    assert settings.model_dump().get("WHISPER_STREAMING_CHUNK_MS") == 1000


@pytest.mark.parametrize("invalid", [320, 560, 1000.0, True, 0])
def test_invalid_whisper_decode_intervals_cannot_start_recordings(whisper, monkeypatch, invalid):
    monkeypatch.setitem(stt.config.__dict__, "WHISPER_STREAMING_CHUNK_MS", invalid)
    with pytest.raises(ValueError, match="WHISPER_STREAMING_CHUNK_MS"):
        stt.validate_provider("whisper")


def test_blank_whisper_streaming_endpoint_cannot_fall_back_to_batch(whisper, monkeypatch):
    monkeypatch.setitem(stt.config.__dict__, "WHISPER_STREAMING_ENDPOINT", " ")
    with pytest.raises(ValueError, match="WHISPER_STREAMING_ENDPOINT"):
        stt.validate_provider("whisper")


def test_blank_whisper_streaming_region_uses_application_region(whisper, monkeypatch):
    monkeypatch.setitem(stt.config.__dict__, "WHISPER_STREAMING_REGION", " ")
    assert stt.active_region_for("whisper") == "us-east-1"


@pytest.mark.parametrize("text,language", [
    ("  회의를 시작합니다.  ", " ko "),
    ("  Let us begin.  ", " en "),
])
def test_whisper_hypotheses_are_clean_text_and_language(text, language):
    result = stt.parse_streaming_response("whisper", {"text": text, "language": language})
    assert result == stt.STTResult(text.strip(), language.strip())


@pytest.mark.parametrize("payload", [
    {"text": ["batch response"], "language": "en"},
    {"text": 12, "language": "en"},
    {"text": "hello", "language": ["en"]},
])
def test_whisper_streaming_rejects_malformed_hypotheses(payload):
    with pytest.raises(ValueError):
        stt.parse_streaming_response("whisper", payload)


@pytest.mark.parametrize("endpoint_status,instances,expected", [
    ("InService", 1, "ready"),
    ("InService", 0, "starting"),
    ("Updating", 1, "starting"),
    ("Failed", 0, "error"),
])
async def test_whisper_streaming_capability_requires_ready_endpoint(
    whisper, monkeypatch, endpoint_status, instances, expected,
):
    observations = []

    @asynccontextmanager
    async def client(*, region_name):
        async def describe_endpoint(**request):
            observations.append((region_name, request["EndpointName"]))
            return {
                "EndpointStatus": endpoint_status,
                "ProductionVariants": [{"CurrentInstanceCount": instances}],
            }
        yield SimpleNamespace(describe_endpoint=describe_endpoint)

    monkeypatch.setattr(stt_capacity, "sagemaker_control_client", client)
    manager = CapacityManager()
    status = await manager.start_session("whisper", "recording")
    assert status.get("streaming") is True
    assert status["status"] == expected
    assert status["managed"] is False
    assert observations == [("us-west-2", "whisper-stream")]
    assert manager.sessions["whisper"] == ({"recording"} if expected == "ready" else set())
    assert (await manager.status("whisper")) == status
    assert observations == [("us-west-2", "whisper-stream")]


async def test_disabled_whisper_status_and_prepare_never_contact_cloud(whisper, monkeypatch):
    monkeypatch.setitem(stt.config.__dict__, "WHISPER_STREAMING_ENABLED", False)
    observations = []

    @asynccontextmanager
    async def client():
        observations.append("unexpected cloud call")
        raise AssertionError("Legacy Whisper must not describe or update cloud capacity")
        yield

    manager = CapacityManager(client_factory=client, metrics_client_factory=client)
    for operation in (manager.status, manager.prepare):
        status = await operation("whisper")
        assert status["status"] == "ready"
        assert status["managed"] is False
        assert not status.get("streaming")
    assert observations == []
    assert stt.active_endpoint_for("whisper") == "whisper-batch"


async def test_unconfigured_whisper_streaming_is_unavailable_without_cloud_calls(
    whisper, monkeypatch,
):
    monkeypatch.setitem(stt.config.__dict__, "WHISPER_STREAMING_ENDPOINT", "")
    manager = CapacityManager()
    status = await manager.status("whisper")
    assert status["status"] == "unavailable"
    assert status.get("streaming") is True


@pytest.mark.parametrize("language,chunk_ms,expected_chunk", [
    ("ko-KR", None, 1000), ("en-US", 2000, 2000), ("auto", 1000, 1000),
])
async def test_whisper_transport_sends_protocol_and_flushes_fragmented_short_tail(
    language, chunk_ms, expected_chunk,
):
    connections = []

    @asynccontextmanager
    async def connect(endpoint, region):
        assert (endpoint, region) == ("whisper-stream", "us-west-2")
        connection = ModelConnection(split=True, delayed_end=True)
        connections.append(connection)
        try:
            yield connection
        finally:
            connection.closed = True

    events = []

    async def emit(event):
        events.append(event)

    stream_class = getattr(nemotron_stream, "WhisperStream", None)
    assert stream_class is not None, "Whisper needs its own decode interval over the shared transport"
    options = {} if chunk_ms is None else {"chunk_ms": chunk_ms}
    stream = stream_class(
        "whisper-stream", language=language,
        region="us-west-2", connection_factory=connect, **options,
    )
    stream.start(emit)
    pcm = b"\x01\x02" * 160
    try:
        await stream.send_audio(pcm)
        await eventually(lambda: any(e["type"] == "partial" for e in events))
    finally:
        await stream.close()
    assert stream.error is None
    assert connections[0].controls == [
        {"type": "start", "language": language, "sample_rate": 16000, "chunk_ms": expected_chunk},
        {"type": "end"},
    ]
    assert b"".join(connections[0].audio) == pcm
    assert [e["text"] for e in events if e["type"] == "final"] == ["회의를 시작합니다."]
    assert connections[0].closed


@pytest.fixture
def whisper_service(whisper, monkeypatch):
    connections = []

    @asynccontextmanager
    async def connect(endpoint, region):
        assert (endpoint, region) == ("whisper-stream", "us-west-2")
        connection = ModelConnection()
        connections.append(connection)
        try:
            yield connection
        finally:
            connection.closed = True

    monkeypatch.setattr(
        ws_handler, "WhisperStream",
        lambda **kwargs: nemotron_stream.WhisperStream(**kwargs, connection_factory=connect),
        raising=False,
    )
    monkeypatch.setattr(ws_handler, "_start_flush", lambda *args: None)
    return connections


async def test_whisper_revisions_stay_live_and_unique_final_keeps_recording_snapshot(
    whisper_service, monkeypatch,
):
    ws = FakeWS()
    state = ws_handler.ConnectionState(
        ws=ws, stt_provider="whisper", stt_ready=True,
        source_lang="ko", translation_target="en", meeting_id="original",
    )
    processed = []

    async def process(ws, text, speaker, language, snapshot, timestamp):
        processed.append((text, language, snapshot))

    monkeypatch.setattr(ws_handler, "_process_segment", process)
    ws_handler._start_sagemaker_streaming(state, ws, "whisper-recording")
    try:
        await state.speech_stream.send_audio(b"\x01\x02")
        await eventually(lambda: any(e.get("phase") == "stt_partial" for e in ws.sent))
        await whisper_service[0].emit({
            "type": "partial", "utterance_id": 1, "text": "회의를 시작", "language": "ko",
        })
        await eventually(lambda: ws.sent[-1].get("originalText") == "회의를 시작")
        assert processed == []
        partial = ws.sent[-1]
        assert partial["sttProvider"] == "whisper"
        assert partial["detectedLanguage"] == "ko"
        state.meeting_id = "changed"
        state.source_lang = "en"
        final = {
            "type": "final", "utterance_id": 1,
            "text": "회의를 시작합니다.", "language": "ko",
        }
        await whisper_service[0].emit(final)
        await whisper_service[0].emit(final)
    finally:
        await ws_handler._stop_recording(state, ws, "whisper-recording")
    await asyncio.gather(*list(state.segment_tasks))
    assert len(processed) == 1
    text, language, snapshot = processed[0]
    assert (text, language, snapshot.meeting_id, snapshot.source_lang) == (
        "회의를 시작합니다.", "ko", "original", "ko",
    )
    assert snapshot.stt_stream_id == partial["streamId"]
    assert snapshot.stt_utterance_id == partial["utteranceId"]
    assert snapshot.stt_provider == "whisper"


@pytest.mark.parametrize("source_lang,expected", [("ko", "ko-KR"), ("en", "en-US"), ("auto", "auto")])
async def test_whisper_websocket_routes_language_and_preserves_silent_stop_tail(
    whisper_service, monkeypatch, source_lang, expected,
):
    pcm = b"\x00\x00" * 160
    processed = []

    class RecordingWS(FakeWS):
        def __init__(self):
            super().__init__()
            self.actions = iter([
                {"action": "startRecording", "sttProvider": "whisper",
                 "sourceLang": source_lang, "translationTarget": "ko", "sttStreaming": True},
                {"action": "sendAudio", "audioData": base64.b64encode(wav(pcm)).decode()},
                {"action": "stopRecording"},
            ])

        async def accept(self):
            pass

        async def receive_json(self):
            try:
                return next(self.actions)
            except StopIteration:
                raise WebSocketDisconnect()

    async def authenticate(ws):
        return "test-user", ""

    async def status(provider, **kwargs):
        assert provider == "whisper"
        return {"provider": provider, "status": "ready", "streaming": True}

    async def process(ws, text, speaker, language, snapshot, timestamp):
        processed.append((text, language, snapshot.stt_provider))

    monkeypatch.setattr(ws_handler, "_authenticate", authenticate)
    monkeypatch.setattr(ws_handler.capacity, "status", status)
    monkeypatch.setattr(ws_handler, "_process_segment", process)
    await ws_handler.ws_endpoint(RecordingWS())
    assert len(whisper_service) == 1
    assert whisper_service[0].controls[0]["language"] == expected
    assert whisper_service[0].audio == [pcm]
    assert whisper_service[0].closed
    assert processed == [("회의를 시작합니다.", "ko", "whisper")]


@pytest.mark.parametrize("enabled,request_options", [
    (False, {}),
    (False, {"sttStreaming": True}),
    (True, {}),
    (True, {"sttStreaming": False}),
    (True, {"sttStreaming": "true"}),
    (True, {"sttStreaming": 1}),
])
async def test_whisper_requires_client_opt_in_or_keeps_batch_without_cloud_control(
    whisper_service, monkeypatch, enabled, request_options,
):
    monkeypatch.setitem(stt.config.__dict__, "WHISPER_STREAMING_ENABLED", enabled)
    # An older browser's six-second WAV is larger than a streaming audio frame.
    pcm = b"\x00\x10" * 96000
    audio = wav(pcm)
    requests = []
    control_calls = []

    class RecordingWS(FakeWS):
        def __init__(self):
            super().__init__()
            self.actions = iter([
                {"action": "startRecording", "sttProvider": "whisper",
                 "sourceLang": "ko", "translationTarget": "ko", **request_options},
                {"action": "sendAudio", "audioData": base64.b64encode(audio).decode()},
                {"action": "stopRecording"},
            ])

        async def accept(self):
            pass

        async def receive_json(self):
            try:
                return next(self.actions)
            except StopIteration:
                raise WebSocketDisconnect()

    async def authenticate(ws):
        return "test-user", ""

    async def invoke_endpoint(**request):
        requests.append(request)

        async def read():
            return json.dumps({"text": ["회의를 시작합니다."], "language": "ko"}).encode()

        return {"Body": SimpleNamespace(read=read)}

    @asynccontextmanager
    async def runtime(*, region_name):
        assert region_name == "us-east-1"
        yield SimpleNamespace(invoke_endpoint=invoke_endpoint)

    @asynccontextmanager
    async def control(**kwargs):
        control_calls.append(kwargs)
        raise AssertionError("Legacy recording must not check capacity")
        yield

    monkeypatch.setattr(ws_handler, "_authenticate", authenticate)
    monkeypatch.setattr(stt, "sagemaker_client", runtime)
    monkeypatch.setattr(stt_capacity, "sagemaker_control_client", control)
    ws = RecordingWS()
    await ws_handler.ws_endpoint(ws)
    assert whisper_service == []
    assert control_calls == []
    assert len(requests) == 1
    assert requests[0]["EndpointName"] == "whisper-batch"
    assert json.loads(requests[0]["Body"]) == {
        "audio_input": audio.hex(), "language": "korean", "task": "transcribe",
    }
    assert [event["phase"] for event in ws.sent] == ["stt", "done"]
    assert all(event["originalText"] == "회의를 시작합니다." for event in ws.sent)


async def test_whisper_failure_releases_only_its_recording(whisper_service):
    ws = FakeWS()
    state = ws_handler.ConnectionState(ws=ws, stt_provider="whisper", stt_ready=True)
    ws_handler.capacity.acquire("whisper", "whisper-failed")
    ws_handler.capacity.acquire("qwen3-asr", "qwen-running")
    try:
        ws_handler._start_sagemaker_streaming(state, ws, "whisper-failed")
        # 일시 오류는 다시 연결한다 — 재연결이 전부 실패해야 녹음을 막는다.
        for attempt in range(ws_handler.STREAM_MAX_RESTARTS + 1):
            await eventually(lambda: len(whisper_service) == attempt + 1)
            await whisper_service[attempt].emit({"type": "error", "message": "GPU failed"})
        await eventually(lambda: not state.stt_ready)
        await eventually(lambda: state.speech_stream.done)
        assert "whisper-failed" not in ws_handler.capacity.sessions["whisper"]
        assert "qwen-running" in ws_handler.capacity.sessions["qwen3-asr"]
        status = next(e for e in ws.sent if e["type"] == "stt_status")
        assert status["provider"] == "whisper"
        assert status["streaming"] and status["recordingBlocked"]
    finally:
        ws_handler.capacity.release("whisper", "whisper-failed")
        ws_handler.capacity.release("qwen3-asr", "qwen-running")
        if state.speech_stream is not None:
            await state.speech_stream.close()
