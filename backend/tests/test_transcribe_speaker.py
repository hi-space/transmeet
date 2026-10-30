"""Transcribe 는 화자 분리 없이 녹음 시작 시 화자로 모든 발화를 남긴다."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amazon_transcribe.model import Alternative, Item, Result, Transcript, TranscriptEvent

from app import ws_handler


def word(content, speaker=None):
    return Item(item_type="pronunciation", content=content, speaker=speaker)


@pytest.mark.parametrize("source_lang", ["en", "ko", "auto"])
async def test_streaming_keeps_one_speaker_in_each_language_mode(monkeypatch, source_lang):
    """AWS 경계만 대체하고 요청 옵션부터 실제 자막 이벤트까지 실행한다."""
    ws = SimpleNamespace(send_json=AsyncMock())
    state = ws_handler.ConnectionState(
        ws=ws, source_lang=source_lang, stt_provider="transcribe", translation_timing="realtime",
    )
    state.audio_queue.put_nowait(None)
    requests = []
    texts = ("출시를 준비했습니다.", "검토를 완료했습니다.") if source_lang == "ko" else (
        "We are ready to launch.", "The review is complete.",
    )

    async def start_stream(**kwargs):
        requests.append(kwargs)
        # 라벨이 섞여 와도 발화를 나누지 않는다.
        labels = ["0", "1"]
        alt = Alternative(
            transcript=" ".join(texts),
            items=[word(text, label) for text, label in zip(texts, labels)],
            entities=[],
        )

        async def events():
            yield TranscriptEvent(Transcript([Result(
                result_id="result-1", is_partial=False, alternatives=[alt],
                language_code="ko-KR" if source_lang == "ko" else "en-US",
            )]))

        return SimpleNamespace(
            input_stream=SimpleNamespace(send_audio_event=AsyncMock(), end_stream=AsyncMock()),
            output_stream=events(),
        )

    async def converse_stream(**kwargs):
        async def events():
            yield {"contentBlockDelta": {"delta": {"text": "Translated."}}}

        return {"stream": events()}

    @asynccontextmanager
    async def bedrock():
        yield SimpleNamespace(converse_stream=converse_stream)

    monkeypatch.setattr(ws_handler, "TranscribeStreamingClient", lambda **kwargs: SimpleNamespace(
        start_stream_transcription=start_stream,
    ))
    monkeypatch.setattr(ws_handler, "bedrock_client", bedrock)

    await asyncio.wait_for(ws_handler._run_transcribe_streaming(state, ws, "test-connection"), timeout=2)
    if state.segment_tasks:
        await asyncio.gather(*list(state.segment_tasks))

    final_events = [call.args[0] for call in ws.send_json.call_args_list if call.args[0].get("phase") == "done"]
    assert [(event["originalText"], event["speaker"]) for event in final_events] == [
        (" ".join(texts), "speaker1"),
    ]
    assert len(requests) == 1
    assert "show_speaker_label" not in requests[0]
    if source_lang == "auto":
        assert requests[0]["language_code"] is None
        assert requests[0]["identify_multiple_languages"] is True
        assert not requests[0].get("identify_language")
        assert requests[0]["language_options"] == ["en-US", "ko-KR"]
    else:
        assert requests[0]["language_code"] == {"en": "en-US", "ko": "ko-KR"}[source_lang]
    assert state.last_confirmed_speaker == "speaker1"


async def test_multilingual_stream_preserves_language_without_splitting_speakers(monkeypatch):
    ws = SimpleNamespace(send_json=AsyncMock())
    state = ws_handler.ConnectionState(
        ws=ws, source_lang="auto", stt_provider="transcribe", translation_timing="realtime",
    )
    state.audio_queue.put_nowait(None)
    requests, prompts = [], []
    speech = [
        ("ko-KR", "출시 계획을 검토하겠습니다.", "0"),
        ("en-US", "The rollout starts tomorrow.", "1"),
        ("ko-KR", "검토 후 다시 알려드리겠습니다.", "0"),
    ]

    async def start_stream(**kwargs):
        requests.append(kwargs)

        async def events():
            for index, (language, text, speaker) in enumerate(speech):
                for partial in (True, False):
                    yield TranscriptEvent(Transcript([Result(
                        result_id=f"result-{index}", is_partial=partial, language_code=language,
                        alternatives=[Alternative(
                            transcript=text, items=[word(text, speaker)], entities=[],
                        )],
                    )]))

        return SimpleNamespace(
            input_stream=SimpleNamespace(send_audio_event=AsyncMock(), end_stream=AsyncMock()),
            output_stream=events(),
        )

    async def converse_stream(**kwargs):
        prompts.append(kwargs["messages"][0]["content"][0]["text"])

        async def events():
            yield {"contentBlockDelta": {"delta": {"text": "Translated."}}}

        return {"stream": events()}

    @asynccontextmanager
    async def bedrock():
        yield SimpleNamespace(converse_stream=converse_stream)

    monkeypatch.setattr(ws_handler, "TranscribeStreamingClient", lambda **kwargs: SimpleNamespace(
        start_stream_transcription=start_stream,
    ))
    monkeypatch.setattr(ws_handler, "bedrock_client", bedrock)

    await asyncio.wait_for(ws_handler._run_transcribe_streaming(state, ws, "mixed"), timeout=2)
    if state.segment_tasks:
        await asyncio.gather(*list(state.segment_tasks))

    assert requests[0]["identify_multiple_languages"] is True
    assert not requests[0].get("identify_language")
    assert requests[0]["language_options"] == ["en-US", "ko-KR"]
    assert "show_speaker_label" not in requests[0]
    events = [call.args[0] for call in ws.send_json.call_args_list]
    finals = [event for event in events if event.get("phase") == "done"]
    partials = [event for event in events if event.get("phase") == "stt_partial"]
    assert [(event["originalText"], event["speaker"], event["detectedLanguage"]) for event in finals] == [
        (speech[0][1], "speaker1", "ko"),
        (speech[1][1], "speaker1", "en"),
        (speech[2][1], "speaker1", "ko"),
    ]
    assert [event["detectedLanguage"] for event in partials] == ["ko", "en", "ko"]
    assert ["Korean text to English" in prompts[0],
            "English text to Korean" in prompts[1],
            "Korean text to English" in prompts[2]] == [True, True, True]
