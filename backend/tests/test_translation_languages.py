"""Exercise real subtitle/translation behavior across the language boundary."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import stt, ws_handler


@pytest.fixture
def translation_service(monkeypatch):
    ws = SimpleNamespace(send_json=AsyncMock())
    prompts = []

    async def converse_stream(**kwargs):
        prompts.append(kwargs["messages"][0]["content"][0]["text"])

        async def events():
            yield {"contentBlockDelta": {"delta": {"text": "Translated result."}}}

        return {"stream": events()}

    @asynccontextmanager
    async def bedrock():
        yield SimpleNamespace(converse_stream=converse_stream)

    monkeypatch.setattr(ws_handler, "bedrock_client", bedrock)
    return ws, prompts


def final_events(ws):
    return [call.args[0] for call in ws.send_json.call_args_list if call.args[0].get("phase") == "done"]


@pytest.mark.parametrize("provider", ["whisper", "qwen3-asr", "nemotron"])
@pytest.mark.parametrize(
    "requested,reported,text,expected",
    [
        ("ko", "en-US", "The release is ready for review.", "en"),
        ("en", "Korean", "배포 검토를 시작하겠습니다.", "ko"),
    ],
)
async def test_detected_speech_survives_a_different_input_preference(
    monkeypatch, translation_service, provider, requested, reported, text, expected,
):
    ws, prompts = translation_service
    state = ws_handler.ConnectionState(ws=ws, stt_provider=provider, source_lang=requested)
    state.translation_target = "auto"

    async def transcribe(*args):
        return stt.STTResult(text, reported)

    monkeypatch.setattr(ws_handler, "transcribe_audio", transcribe)
    await ws_handler._process_buffered_audio(ws, b"audio", state, "test")

    finals = final_events(ws)
    assert len(finals) == 1
    assert finals[0]["originalText"] == text
    assert finals[0]["detectedLanguage"] == expected
    assert ("English text to Korean" if expected == "en" else "Korean text to English") in prompts[0]
    stt_event = next(call.args[0] for call in ws.send_json.call_args_list if call.args[0].get("phase") == "stt")
    assert stt_event["detectedLanguage"] == expected


@pytest.mark.parametrize(
    "source,destination,expected",
    [
        ("ko", "auto", "Korean text to English"),
        ("en", "auto", "English text to Korean"),
        ("ko", "en", "Korean text to English"),
        ("en", "ko", "English text to Korean"),
    ],
)
async def test_independent_destination_controls_the_translation_prompt(
    translation_service, source, destination, expected,
):
    ws, prompts = translation_service
    state = ws_handler.ConnectionState(ws=ws, source_lang="auto")
    state.translation_target = destination
    await ws_handler._process_segment(ws, "회의 준비 / meeting preparation", "speaker1", source, state, "now")
    assert len(prompts) == 1
    assert expected in prompts[0]
    assert final_events(ws)[0]["translatedText"] == "Translated result."


@pytest.mark.parametrize("language,text", [
    ("ko", "검토를 완료했습니다."),
    ("en", "The review is complete."),
])
async def test_same_language_destination_preserves_original_and_still_saves(
    monkeypatch, translation_service, language, text,
):
    ws, prompts = translation_service
    state = ws_handler.ConnectionState(ws=ws, source_lang="auto", meeting_id="meeting")
    state.translation_target = language
    saved = []

    async def transact_write_items(**kwargs):
        saved.append(kwargs)

    @asynccontextmanager
    async def dynamodb():
        yield SimpleNamespace(transact_write_items=transact_write_items)

    monkeypatch.setattr(ws_handler, "dynamodb_client", dynamodb)
    await ws_handler._process_segment(ws, text, "speaker1", language, state, "now")

    assert prompts == []
    assert final_events(ws)[0]["translatedText"] == text
    record = saved[0]["TransactItems"][0]["Put"]["Item"]
    assert record["originalText"]["S"] == record["translatedText"]["S"] == text
    assert record["detectedLanguage"]["S"] == language
    assert record["targetLanguage"]["S"] == language
    assert final_events(ws)[0]["targetLanguage"] == language


async def test_manual_same_language_destination_preserves_original(translation_service):
    ws, prompts = translation_service
    await ws_handler._retranslate_segment(
        ws, "message", "검토를 완료했습니다.", "speaker1", "ko", "ko", "model", "now",
    )
    assert prompts == []
    assert final_events(ws)[0]["translatedText"] == "검토를 완료했습니다."


@pytest.mark.parametrize("source,target,expected", [
    ("auto", "ko", "Korean text to English"),
    ("ko", "en", "Korean text to English"),
])
async def test_legacy_states_keep_their_existing_direction(translation_service, source, target, expected):
    ws, prompts = translation_service
    state = ws_handler.ConnectionState(ws=ws, source_lang=source, target_lang=target)
    await ws_handler._process_segment(ws, "검토를 완료했습니다.", "speaker1", "ko", state, "now")
    assert expected in prompts[0]


@pytest.mark.parametrize("provider,expected_source", [
    ("whisper", "ko"), ("qwen3-asr", "auto"),
])
def test_recording_start_passes_independent_settings_to_the_real_pipeline(
    monkeypatch, provider, expected_source,
):
    observed = []

    async def authenticate(ws):
        return "user", ""

    async def flush(state, ws, connection_id):
        observed.append((state.source_lang, getattr(state, "translation_target", None)))

    monkeypatch.setattr(ws_handler, "_authenticate", authenticate)
    monkeypatch.setattr(stt.config, "WHISPER_ENDPOINT", "test-whisper")
    monkeypatch.setattr(stt.config, "QWEN_ASR_ENDPOINT", "test-qwen")
    monkeypatch.setattr(stt.config, "QWEN_ASR_INFERENCE_COMPONENT", "")
    monkeypatch.setattr(ws_handler, "_flush_audio_buffer", flush)
    app = FastAPI()
    app.include_router(ws_handler.router)
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({
            "action": "startRecording", "sttProvider": provider,
            "sourceLang": "ko", "translationTarget": "ko",
        })
        ws.send_json({"action": "stopRecording"})
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["type"] == "pong"
    assert (expected_source, "ko") in observed


async def test_buffered_utterance_keeps_its_translation_target_when_settings_change(
    monkeypatch, translation_service,
):
    ws, prompts = translation_service
    state = ws_handler.ConnectionState(ws=ws, source_lang="auto")
    state.translation_target = "ko"
    state.audio_buffer.append(b"audio")
    started, release = asyncio.Event(), asyncio.Event()

    async def transcribe(*args):
        started.set()
        await release.wait()
        return stt.STTResult("검토를 완료했습니다.", "ko")

    monkeypatch.setattr(ws_handler, "transcribe_audio", transcribe)
    pending = asyncio.create_task(ws_handler._flush_audio_buffer(state, ws, "test"))
    await asyncio.wait_for(started.wait(), 1)
    state.translation_target = "en"
    release.set()
    await asyncio.wait_for(pending, 1)
    assert prompts == []
    assert final_events(ws)[0]["translatedText"] == "검토를 완료했습니다."
