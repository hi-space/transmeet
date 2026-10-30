"""Streaming subtitles and recording lifetime, without network AWS calls."""
import asyncio
import base64
import io
import wave
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from app import nemotron_stream, stt, ws_handler
from tests.test_nemotron_stream import ModelService, eventually


class FakeWS:
    def __init__(self):
        self.sent = []
        self.disconnected = False

    async def send_json(self, event):
        if self.disconnected:
            raise WebSocketDisconnect()
        self.sent.append(event)


@pytest.fixture
def streaming(monkeypatch):
    service = ModelService()
    monkeypatch.setattr(stt.config, "NEMOTRON_STREAMING_ENABLED", True)
    monkeypatch.setattr(stt.config, "NEMOTRON_STREAMING_ENDPOINT", "stream-endpoint")
    monkeypatch.setattr(stt.config, "NEMOTRON_STREAMING_CHUNK_MS", 560)
    monkeypatch.setattr(stt.config, "NEMOTRON_ASR_ENDPOINT", "retained-batch")
    monkeypatch.setattr(stt.config, "NEMOTRON_ASR_INFERENCE_COMPONENT", "retained-batch-ic")
    monkeypatch.setattr(ws_handler, "NemotronStream", lambda **kwargs: nemotron_stream.NemotronStream(
        **kwargs, connection_factory=service.connect,
    ), raising=False)
    monkeypatch.setattr(ws_handler, "_start_flush", lambda *args: None)
    return service


async def test_partial_is_live_only_and_duplicate_final_is_translated_once(streaming, monkeypatch):
    ws = FakeWS()
    state = ws_handler.ConnectionState(
        ws=ws, stt_provider="nemotron", stt_ready=True, source_lang="ko", translation_target="en",
    )
    processed = []

    async def process(ws, text, speaker, language, snapshot, timestamp):
        processed.append((text, language, snapshot.translation_target))

    monkeypatch.setattr(ws_handler, "_process_segment", process)
    ws_handler._start_nemotron_streaming(state, ws, "recording")
    await state.nemotron_stream.send_audio(b"\x01\x02")
    await eventually(lambda: any(event.get("phase") == "stt_partial" for event in ws.sent))
    assert processed == []
    assert ws.sent[-1]["originalText"] == "회의를"
    final = {"type": "final", "utterance_id": 1, "text": "회의를 시작합니다.", "language": "ko-KR"}
    await streaming.connections[0].emit(final)
    await streaming.connections[0].emit(final)
    await ws_handler._stop_recording(state, ws, "recording")
    await asyncio.gather(*list(state.segment_tasks))
    assert processed == [("회의를 시작합니다.", "ko", "en")]
    assert streaming.connections[0].closed


async def test_empty_final_clears_partial_without_saving_a_segment(streaming, monkeypatch):
    ws = FakeWS()
    state = ws_handler.ConnectionState(ws=ws, stt_provider="nemotron", source_lang="ko")
    processed = []

    async def process(*args):
        processed.append(args)

    monkeypatch.setattr(ws_handler, "_process_segment", process)
    ws_handler._start_nemotron_streaming(state, ws, "recording")
    await state.nemotron_stream.send_audio(b"\x00\x00")
    await eventually(lambda: any(event.get("originalText") == "회의를" for event in ws.sent))
    await streaming.connections[0].emit({
        "type": "final", "utterance_id": 1, "text": "", "language": "auto",
    })
    await eventually(lambda: ws.sent[-1].get("originalText") == "")
    await ws_handler._stop_recording(state, ws, "recording")
    assert processed == []
    assert ws.sent[-1]["phase"] == "stt_partial"
    assert ws.sent[-1]["utteranceId"] == 1


async def test_slow_translation_does_not_block_new_hypotheses_or_change_old_settings(
    streaming, monkeypatch,
):
    ws = FakeWS()
    state = ws_handler.ConnectionState(
        ws=ws, stt_provider="nemotron", source_lang="ko", translation_target="en", meeting_id="old",
    )
    started, release = asyncio.Event(), asyncio.Event()
    processed = []

    async def process(ws, text, speaker, language, snapshot, timestamp):
        started.set()
        await release.wait()
        processed.append((snapshot.meeting_id, snapshot.source_lang, snapshot.translation_target, language))

    monkeypatch.setattr(ws_handler, "_process_segment", process)
    ws_handler._start_nemotron_streaming(state, ws, "recording")
    await eventually(lambda: streaming.connections)
    await streaming.connections[0].emit({
        "type": "final", "utterance_id": 1, "text": "회의를 시작합니다.", "language": "ko-KR",
    })
    await asyncio.wait_for(started.wait(), 1)
    state.meeting_id = "new"
    state.source_lang = "en"
    state.translation_target = "ko"
    await streaming.connections[0].emit({
        "type": "partial", "utterance_id": 2, "text": "새 안건", "language": "ko-KR",
    })
    await eventually(lambda: any(event.get("originalText") == "새 안건" for event in ws.sent))
    await asyncio.wait_for(ws_handler._stop_recording(state, ws, "recording"), 1)
    release.set()
    await asyncio.gather(*list(state.segment_tasks))
    assert processed == [("old", "ko", "en", "ko")]


async def test_transient_stream_error_reconnects_without_stopping_the_recording(streaming):
    """Qwen 'speech stream timed out' 같은 일시 오류로 녹음 전체가 멈추면 안 된다."""
    ws = FakeWS()
    state = ws_handler.ConnectionState(ws=ws, stt_provider="nemotron", stt_ready=True)
    ws_handler.capacity.acquire("nemotron", "recording")
    try:
        ws_handler._start_nemotron_streaming(state, ws, "recording")
        failed = state.speech_stream
        await eventually(lambda: streaming.connections)
        await streaming.connections[0].emit({"type": "error", "message": "speech stream timed out"})
        await eventually(lambda: len(streaming.connections) == 2)
        assert state.speech_stream is not failed
        assert state.stt_ready is True
        assert "recording" in ws_handler.capacity.sessions["nemotron"]
        assert not any(event.get("recordingBlocked") for event in ws.sent)
        assert not any(event["type"] == "error" for event in ws.sent)
        await state.speech_stream.send_audio(b"\x01\x02")
        await eventually(lambda: streaming.connections[1].audio)
    finally:
        await ws_handler._stop_recording(state, ws, "recording")
        ws_handler.capacity.release("nemotron", "recording")


async def test_repeated_stream_failures_block_further_audio_and_release_capacity(streaming):
    ws = FakeWS()
    state = ws_handler.ConnectionState(ws=ws, stt_provider="nemotron", stt_ready=True)
    ws_handler.capacity.acquire("nemotron", "failed-recording")
    ws_handler._start_nemotron_streaming(state, ws, "failed-recording")
    for attempt in range(ws_handler.STREAM_MAX_RESTARTS + 1):
        await eventually(lambda: len(streaming.connections) == attempt + 1)
        assert state.stt_ready is True
        await streaming.connections[attempt].emit({"type": "error", "message": "GPU failed"})
    await eventually(lambda: state.stt_ready is False)
    await eventually(lambda: state.nemotron_stream.done)
    assert len(streaming.connections) == ws_handler.STREAM_MAX_RESTARTS + 1
    assert "failed-recording" not in ws_handler.capacity.sessions["nemotron"]
    assert any(event["type"] == "error" for event in ws.sent)
    assert any(event["type"] == "stt_status" and event.get("recordingBlocked")
               and event.get("streaming") and event["status"] == "error" for event in ws.sent)
    assert all(connection.closed for connection in streaming.connections)


async def test_a_final_resets_the_restart_budget(streaming, monkeypatch):
    ws = FakeWS()
    state = ws_handler.ConnectionState(ws=ws, stt_provider="nemotron", stt_ready=True)

    async def process(*args):
        pass

    monkeypatch.setattr(ws_handler, "_process_segment", process)
    ws_handler._start_nemotron_streaming(state, ws, "recording")
    try:
        # 인식이 정상으로 돌아온 뒤의 오류는 새 오류다 — 예산을 처음부터 다시 쓴다.
        for attempt in range(ws_handler.STREAM_MAX_RESTARTS + 2):
            await eventually(lambda: len(streaming.connections) == attempt + 1)
            await streaming.connections[attempt].emit({
                "type": "final", "utterance_id": 1, "text": f"안건 {attempt}", "language": "ko-KR",
            })
            await streaming.connections[attempt].emit({"type": "error", "message": "decoder behind"})
        await eventually(lambda: len(streaming.connections) == ws_handler.STREAM_MAX_RESTARTS + 3)
        assert state.stt_ready is True
        assert not any(event.get("recordingBlocked") for event in ws.sent)
    finally:
        await ws_handler._stop_recording(state, ws, "recording")


async def test_old_stream_error_does_not_block_or_release_its_replacement(streaming):
    ws = FakeWS()
    state = ws_handler.ConnectionState(ws=ws, stt_provider="nemotron", stt_ready=True)
    ws_handler._start_nemotron_streaming(state, ws, "replacement-recording")
    original = state.nemotron_stream
    await eventually(lambda: streaming.connections)
    state.nemotron_stream = object()
    ws_handler.capacity.acquire("nemotron", "replacement-recording")
    try:
        await streaming.connections[0].emit({"type": "error", "message": "old GPU failed"})
        await eventually(lambda: original.done)
        assert state.stt_ready is True
        assert "replacement-recording" in ws_handler.capacity.sessions["nemotron"]
        assert not any(event.get("recordingBlocked") for event in ws.sent)
    finally:
        ws_handler.capacity.release("nemotron", "replacement-recording")
        await original.close()


async def test_translation_backlog_stops_input_after_preserving_the_overflow_final(
    streaming, monkeypatch,
):
    ws = FakeWS()
    state = ws_handler.ConnectionState(ws=ws, stt_provider="nemotron", stt_ready=True)
    release = asyncio.Event()
    processed = []

    async def process(ws, text, speaker, language, snapshot, timestamp):
        processed.append(text)
        await release.wait()

    monkeypatch.setattr(ws_handler, "_process_segment", process)
    ws_handler._start_nemotron_streaming(state, ws, "recording")
    await eventually(lambda: streaming.connections)
    for number in range(1, 67):
        await streaming.connections[0].emit({
            "type": "final", "utterance_id": number, "text": f"안건 {number}", "language": "ko-KR",
        })
    try:
        await eventually(lambda: state.nemotron_stream.done)
        assert state.stt_ready is False
        assert processed == [f"안건 {number}" for number in range(1, 66)]
        assert len(state.segment_tasks) == 65
        assert any(event["type"] == "error" for event in ws.sent)
    finally:
        release.set()
        await asyncio.gather(*list(state.segment_tasks))
        await ws_handler._stop_recording(state, ws, "recording")


def wav(pcm):
    data = io.BytesIO()
    with wave.open(data, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(pcm)
    return data.getvalue()


def test_unaligned_pcm_in_a_wav_is_rejected_without_discarding_its_last_byte():
    with pytest.raises(ValueError):
        ws_handler._nemotron_pcm(base64.b64encode(wav(b"\x01\x02\x03")).decode())


async def test_websocket_disconnect_flushes_silent_short_tail_and_persists_final(
    streaming, monkeypatch,
):
    saved = []
    pcm = b"\x00\x00" * 160

    class DisconnectingWS(FakeWS):
        def __init__(self):
            super().__init__()
            self.actions = iter([
                {"action": "startRecording", "sttProvider": "nemotron",
                 "sourceLang": "auto", "translationTarget": "ko"},
                {"action": "sendAudio", "audioData": base64.b64encode(wav(pcm)).decode()},
            ])

        async def accept(self):
            pass

        async def receive_json(self):
            try:
                return next(self.actions)
            except StopIteration:
                self.disconnected = True
                raise WebSocketDisconnect()

    async def authenticate(ws):
        return "user", ""

    async def claim(state, meeting_id):
        return "meeting"

    async def status(provider, **kwargs):
        return {"provider": provider, "status": "ready", "managed": False, "streaming": True}

    async def publish():
        pass

    async def update_item(**request):
        pass

    async def transact_write_items(**request):
        # 발언은 메시지 테이블 Put + 회의 항목 갱신 트랜잭션으로 저장된다
        saved.append(request)

    @asynccontextmanager
    async def dynamodb():
        yield SimpleNamespace(update_item=update_item, transact_write_items=transact_write_items)

    monkeypatch.setattr(ws_handler, "_authenticate", authenticate)
    monkeypatch.setattr(ws_handler, "_claim_meeting", claim)
    monkeypatch.setattr(ws_handler.capacity, "status", status)
    monkeypatch.setattr(ws_handler.capacity, "publish_activity", publish)
    monkeypatch.setattr(ws_handler, "dynamodb_client", dynamodb)
    await asyncio.wait_for(ws_handler.ws_endpoint(DisconnectingWS(), meetingId="meeting"), 1)
    await eventually(lambda: saved)
    assert streaming.connections[0].audio == [pcm]
    assert streaming.connections[0].closed
    puts = [op["Put"] for request in saved for op in request["TransactItems"] if "Put" in op]
    assert len(puts) == 1
    assert puts[0]["TableName"] == ws_handler.config.MESSAGES_TABLE
    record = puts[0]["Item"]
    assert record["originalText"]["S"] == "회의를 시작합니다."
    assert record["translatedText"]["S"] == "회의를 시작합니다."
    assert record["detectedLanguage"]["S"] == "ko"
