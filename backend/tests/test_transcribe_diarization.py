"""Transcribe 화자 ID가 변환·병합·스트리밍 과정에서 보존되는지 검증한다."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amazon_transcribe.model import Alternative, Item, Result, Transcript, TranscriptEvent

from app import ws_handler


def word(content, speaker=None):
    return Item(item_type="pronunciation", content=content, speaker=speaker)


def punctuation(content):
    return Item(item_type="punctuation", content=content)


@pytest.mark.parametrize("labels", [("0", "1"), ("spk_0", "spk_1")])
def test_different_speakers_are_not_merged(labels):
    alt = Alternative(
        transcript="Ready? Yes.",
        items=[word("Ready", labels[0]), punctuation("?"), word("Yes", labels[1]), punctuation(".")],
        entities=[],
    )

    segments = ws_handler._extract_segments(alt, True, "speaker1")

    assert ws_handler._merge_consecutive_same_speaker(segments) == [
        ("Ready?", "speaker1"), ("Yes.", "speaker2"),
    ]


def test_speakers_beyond_two_keep_distinct_ids():
    alt = Alternative(
        transcript="First second third eleventh.",
        items=[
            word("First", "0"), word("second", "1"), word("third", "2"),
            word("eleventh", "spk_10"), punctuation("."),
        ],
        entities=[],
    )

    assert ws_handler._extract_segments(alt, True, "speaker1") == [
        ("First", "speaker1"), ("second", "speaker2"),
        ("third", "speaker3"), ("eleventh.", "speaker11"),
    ]


def test_unlabeled_words_do_not_create_a_new_speaker_turn():
    alt = Alternative(
        transcript="We can ship.",
        items=[word("We", "spk_1"), word("can"), word("ship", "1"), punctuation(".")],
        entities=[],
    )

    assert ws_handler._extract_segments(alt, True, "speaker1") == [("We can ship.", "speaker2")]


@pytest.mark.parametrize("label", [None, "", "unknown", "spk_"])
def test_unknown_labels_use_the_supplied_fallback(label):
    alt = Alternative(transcript="Ready.", items=[word("Ready", label), punctuation(".")], entities=[])

    assert ws_handler._extract_segments(alt, True, "speaker3") == [("Ready.", "speaker3")]


@pytest.mark.parametrize("items", [[], [punctuation(".")]])
def test_no_usable_words_preserves_the_transcript(items):
    alt = Alternative(transcript="Keep this transcript.", items=items, entities=[])

    assert ws_handler._extract_segments(alt, True, "speaker2") == [
        ("Keep this transcript.", "speaker2"),
    ]


def test_disabled_diarization_keeps_the_default_speaker():
    alt = Alternative(transcript="Ready? Yes.", items=[word("Ready", "0"), word("Yes", "1")], entities=[])

    assert ws_handler._extract_segments(alt, False, "speaker3") == [("Ready? Yes.", "speaker3")]


@pytest.mark.parametrize("source_lang", ["en", "ko", "auto"])
async def test_streaming_emits_distinct_speakers_in_each_language_mode(monkeypatch, source_lang):
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
        # 화자 분리를 요청하지 않으면 서비스도 화자 라벨을 돌려주지 않는다.
        labels = ["0", "1"] if kwargs.get("show_speaker_label") else [None, None]
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
        (texts[0], "speaker1"), (texts[1], "speaker2"),
    ]
    assert len(requests) == 1
    assert requests[0]["show_speaker_label"] is True
    if source_lang == "auto":
        assert requests[0]["language_code"] is None
        assert requests[0]["identify_multiple_languages"] is True
        assert not requests[0].get("identify_language")
        assert requests[0]["language_options"] == ["en-US", "ko-KR"]
    else:
        assert requests[0]["language_code"] == {"en": "en-US", "ko": "ko-KR"}[source_lang]
    assert state.last_confirmed_speaker == "speaker2"


async def test_multilingual_stream_preserves_language_and_speaker_as_speech_alternates(monkeypatch):
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
    assert requests[0]["show_speaker_label"] is True
    events = [call.args[0] for call in ws.send_json.call_args_list]
    finals = [event for event in events if event.get("phase") == "done"]
    partials = [event for event in events if event.get("phase") == "stt_partial"]
    assert [(event["originalText"], event["speaker"], event["detectedLanguage"]) for event in finals] == [
        (speech[0][1], "speaker1", "ko"),
        (speech[1][1], "speaker2", "en"),
        (speech[2][1], "speaker1", "ko"),
    ]
    assert [event["detectedLanguage"] for event in partials] == ["ko", "en", "ko"]
    assert ["Korean text to English" in prompts[0],
            "English text to Korean" in prompts[1],
            "Korean text to English" in prompts[2]] == [True, True, True]
