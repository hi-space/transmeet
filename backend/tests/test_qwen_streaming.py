"""Qwen recording capability, transport and subtitles at the AWS boundary."""
import asyncio
from contextlib import asynccontextmanager

import pytest

from app import config as config_module, nemotron_stream, stt, ws_handler
from app.stt_capacity import CapacityManager
from tests.test_nemotron_stream import ModelConnection, eventually
from tests.test_nemotron_ws import FakeWS


@pytest.fixture
def qwen(monkeypatch):
    # Raising=False lets the first red run reach the missing capability itself.
    for name, value in {
        "QWEN_STREAMING_ENABLED": True,
        "QWEN_STREAMING_ENDPOINT": "qwen-stream",
        "QWEN_STREAMING_REGION": "ap-northeast-2",
        "QWEN_STREAMING_CHUNK_MS": 1000,
        "QWEN_ASR_ENDPOINT": "qwen-batch",
        "QWEN_ASR_INFERENCE_COMPONENT": "qwen-batch-ic",
    }.items():
        monkeypatch.setattr(type(stt.config), name, value, raising=False)
        monkeypatch.setitem(stt.config.__dict__, name, value)
    return []


def test_qwen_streaming_config_and_active_endpoint_do_not_change_batch_routing(qwen):
    assert "QWEN_STREAMING_ENABLED" in config_module.Config.model_fields
    assert stt.is_streaming_provider("qwen3-asr")
    assert stt.active_endpoint_for("qwen3-asr") == "qwen-stream"
    assert stt.active_region_for("qwen3-asr") == "ap-northeast-2"
    assert stt.active_inference_component_for("qwen3-asr") == ""
    assert stt.endpoint_for("qwen3-asr") == "qwen-batch"
    assert stt.inference_component_for("qwen3-asr") == "qwen-batch-ic"


def test_qwen_disabled_streaming_retains_batch_and_invalid_chunk_is_rejected(qwen, monkeypatch):
    monkeypatch.setitem(stt.config.__dict__, "QWEN_STREAMING_ENABLED", False)
    assert not stt.is_streaming_provider("qwen3-asr")
    assert stt.active_endpoint_for("qwen3-asr") == "qwen-batch"
    monkeypatch.setitem(stt.config.__dict__, "QWEN_STREAMING_ENABLED", True)
    monkeypatch.setitem(stt.config.__dict__, "QWEN_STREAMING_CHUNK_MS", 560)
    with pytest.raises(ValueError, match="QWEN_STREAMING_CHUNK_MS"):
        stt.active_endpoint_for("qwen3-asr")


async def test_qwen_real_capacity_status_advertises_streaming_to_browser(qwen, monkeypatch):
    from types import SimpleNamespace

    endpoints = []

    async def describe_endpoint(**kwargs):
        endpoints.append(kwargs["EndpointName"])
        return {"EndpointStatus": "InService", "ProductionVariants": [{"CurrentInstanceCount": 1}]}

    @asynccontextmanager
    async def client():
        yield SimpleNamespace(describe_endpoint=describe_endpoint)

    manager = CapacityManager(client_factory=client)
    status = await manager.status("qwen3-asr")
    assert status["status"] == "ready"
    assert status.get("streaming") is True
    assert not status["managed"]
    assert endpoints == ["qwen-stream"]
    assert (await manager.status("qwen3-asr"))["streaming"] is True


async def test_qwen_uses_seoul_and_flushes_a_short_tail_with_one_second_model_chunks(qwen):
    connections = []

    @asynccontextmanager
    async def connect(endpoint, region):
        assert (endpoint, region) == ("qwen-stream", "ap-northeast-2")
        connection = ModelConnection(split=True)
        connections.append(connection)
        try:
            yield connection
        finally:
            connection.closed = True

    events = []

    async def emit(event):
        events.append(event)

    stream = nemotron_stream.QwenStream(
        "qwen-stream", language="ko-KR", region="ap-northeast-2", connection_factory=connect,
    )
    stream.start(emit)
    await stream.send_audio(b"\x01\x02" * 160)
    await eventually(lambda: any(e["type"] == "partial" for e in events))
    await stream.close()
    assert stream.error is None
    assert connections[0].controls[0]["chunk_ms"] == 1000
    assert b"".join(connections[0].audio) == b"\x01\x02" * 160
    assert [e["text"] for e in events if e["type"] == "final"] == ["회의를 시작합니다."]


@pytest.fixture
def qwen_service(qwen, monkeypatch):
    connections = []

    @asynccontextmanager
    async def connect(endpoint, region):
        assert (endpoint, region) == ("qwen-stream", "ap-northeast-2")
        connection = ModelConnection()
        connections.append(connection)
        try:
            yield connection
        finally:
            connection.closed = True

    monkeypatch.setattr(ws_handler, "QwenStream", lambda **kwargs: nemotron_stream.QwenStream(
        **kwargs, connection_factory=connect,
    ), raising=False)
    monkeypatch.setattr(ws_handler, "_start_flush", lambda *args: None)
    return connections


async def test_qwen_partial_is_not_saved_and_final_retains_recording_identity(qwen_service, monkeypatch):
    ws = FakeWS()
    state = ws_handler.ConnectionState(
        ws=ws, stt_provider="qwen3-asr", stt_ready=True,
        source_lang="ko", translation_target="en", meeting_id="original",
    )
    processed = []

    async def process(ws, text, speaker, language, snapshot, timestamp):
        processed.append((text, language, snapshot))

    monkeypatch.setattr(ws_handler, "_process_segment", process)
    ws_handler._start_sagemaker_streaming(state, ws, "qwen-recording")
    await state.speech_stream.send_audio(b"\x01\x02")
    await eventually(lambda: any(e.get("phase") == "stt_partial" for e in ws.sent))
    assert processed == []
    partial = next(e for e in ws.sent if e.get("phase") == "stt_partial")
    assert partial["sttProvider"] == "qwen3-asr"
    state.meeting_id = "changed"
    state.source_lang = "en"
    final = {"type": "final", "utterance_id": 1, "text": "회의를 시작합니다.", "language": "Korean"}
    await qwen_service[0].emit(final)
    await qwen_service[0].emit(final)
    await ws_handler._stop_recording(state, ws, "qwen-recording")
    await asyncio.gather(*list(state.segment_tasks))
    assert len(processed) == 1
    text, language, snapshot = processed[0]
    assert (text, language, snapshot.meeting_id, snapshot.source_lang) == (
        "회의를 시작합니다.", "ko", "original", "ko",
    )
    assert snapshot.stt_stream_id == partial["streamId"]
    assert snapshot.stt_provider == "qwen3-asr"


async def test_qwen_failure_releases_only_qwen_capacity(qwen_service):
    ws = FakeWS()
    state = ws_handler.ConnectionState(ws=ws, stt_provider="qwen3-asr", stt_ready=True)
    ws_handler.capacity.acquire("qwen3-asr", "qwen-failed")
    ws_handler.capacity.acquire("nemotron", "nemotron-running")
    try:
        ws_handler._start_sagemaker_streaming(state, ws, "qwen-failed")
        # 일시 오류는 다시 연결한다 — 재연결이 전부 실패해야 녹음을 막는다.
        for attempt in range(ws_handler.STREAM_MAX_RESTARTS + 1):
            await eventually(lambda: len(qwen_service) == attempt + 1)
            await qwen_service[attempt].emit({"type": "error", "message": "GPU failed"})
        await eventually(lambda: not state.stt_ready)
        await eventually(lambda: state.speech_stream.done)
        assert "qwen-failed" not in ws_handler.capacity.sessions["qwen3-asr"]
        assert "nemotron-running" in ws_handler.capacity.sessions["nemotron"]
        status = next(e for e in ws.sent if e["type"] == "stt_status")
        assert status["provider"] == "qwen3-asr"
        assert status["streaming"] and status["recordingBlocked"]
    finally:
        ws_handler.capacity.release("nemotron", "nemotron-running")


async def test_qwen_final_messages_identify_qwen_in_the_translation_pipeline(monkeypatch):
    ws = FakeWS()
    state = ws_handler.ConnectionState(
        ws=ws, stt_provider="qwen3-asr", source_lang="ko", translation_target="ko",
        stt_stream_id="qwen-recording:1", stt_utterance_id=2,
    )
    await ws_handler._process_segment(ws, "회의를 시작합니다.", "speaker1", "ko", state, "now")
    assert [e["phase"] for e in ws.sent] == ["stt", "done"]
    assert all(e["sttProvider"] == "qwen3-asr" for e in ws.sent)


@pytest.mark.parametrize("language, expected", [("ko", "ko-KR"), ("en", "en-US")])
async def test_websocket_recording_passes_selected_qwen_language_to_model(
    qwen_service, monkeypatch, language, expected,
):
    from fastapi import WebSocketDisconnect

    class RecordingWS(FakeWS):
        def __init__(self):
            super().__init__()
            self.actions = iter([
                {"action": "startRecording", "sttProvider": "qwen3-asr",
                 "sourceLang": language, "translationTarget": "ko"},
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
        return {"provider": provider, "status": "ready", "streaming": True}

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(ws_handler, "_authenticate", authenticate)
    monkeypatch.setattr(ws_handler.capacity, "status", status)
    monkeypatch.setattr(ws_handler.capacity, "publish_activity", noop)
    monkeypatch.setattr(ws_handler, "_process_segment", noop)
    await ws_handler.ws_endpoint(RecordingWS())
    assert qwen_service[0].controls[0]["language"] == expected
