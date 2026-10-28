import json
from datetime import datetime, timezone

from app.page_agent import build_meeting_payload, merge_messages

MODEL = "global.anthropic.claude-opus-5-5"


def msg(ts, speaker, orig, tr="", lang="en", mid=None):
    return {
        "id": mid or ts,
        "timestamp": ts,
        "speaker": speaker,
        "originalText": orig,
        "translatedText": tr,
        "detectedLanguage": lang,
    }


def test_merge_joins_same_speaker_within_10s_and_keeps_last_timestamp():
    out = merge_messages([
        msg("2026-10-06T10:00:00+09:00", "speaker1", "Hello", "안녕"),
        msg("2026-10-06T10:00:05+09:00", "speaker1", "again", "다시"),
        msg("2026-10-06T10:00:30+09:00", "speaker1", "later", "나중"),
        msg("2026-10-06T10:00:31+09:00", "me", "me", "나"),
    ])
    assert [m["original"] for m in out] == ["Hello again", "later", "me"]
    assert out[0]["translation"] == "안녕 다시"
    assert out[0]["timestamp"] == "2026-10-06T10:00:05+09:00"


def test_payload_sorts_messages_by_timestamp():
    item = {
        "meetingId": "m1",
        "title": "Meeting 2026-10-06",
        "createdAt": "2026-10-06T09:59:00+09:00",
        "messages": [
            msg("2026-10-06T10:05:00+09:00", "me", "second"),
            msg("2026-10-06T10:00:00+09:00", "speaker1", "first"),
        ],
    }
    p = build_meeting_payload(item, model_id=MODEL)
    assert [m["original"] for m in p["messages"]] == ["first", "second"]
    assert p["meeting"]["startedAt"] == "2026-10-06T09:59:00+09:00"
    assert p["meeting"]["endedAt"] == "2026-10-06T10:05:00+09:00"


def test_payload_metadata_fields():
    item = {
        "meetingId": "m1",
        "title": "배포 파이프라인",
        "createdAt": "2026-10-06T10:00:00+09:00",
        "messages": [
            msg("2026-10-06T10:00:10+09:00", "speaker1", "Hi", "안녕", "en"),
            msg("2026-10-06T10:00:40+09:00", "me", "네", "Yes", "ko"),
        ],
    }
    now = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)
    p = build_meeting_payload(item, model_id=MODEL, now=now)
    m = p["meeting"]
    assert m["meetingId"] == "m1" and m["title"] == "배포 파이프라인"
    assert m["speakers"] == ["Speaker 1", "Me"]
    assert m["languages"] == ["en", "ko"]
    assert m["generatedAt"] == "2026-10-08T00:00:00+00:00"
    assert m["modelId"] == MODEL
    assert p["messages"][0]["speaker"] == "speaker1"
    assert set(p["messages"][0]) == {"timestamp", "speaker", "original", "translation", "detectedLanguage"}


def test_inline_messages_override_stored_messages():
    item = {"meetingId": "m1", "title": "t", "createdAt": "2026-10-06T10:00:00+09:00", "messages": []}
    inline = [
        {"speaker": "speaker1", "original": "live one", "translation": "라이브", "timestamp": "2026-10-06T10:01:00+09:00"},
        {"speaker": "speaker1", "original": "no ts"},
    ]
    p = build_meeting_payload(item, inline_messages=inline, model_id=MODEL)
    # 시각이 없는 메시지는 직전 시각을 이어 쓰므로 같은 화자 연속 발언으로 병합된다
    assert [m["original"] for m in p["messages"]] == ["live one no ts"]
    assert p["messages"][0]["timestamp"] == "2026-10-06T10:01:00+09:00"


def test_payload_preserves_additional_speakers():
    item = {
        "meetingId": "m1",
        "title": "Team meeting",
        "messages": [
            msg("2026-10-06T10:00:00+09:00", "speaker3", "Third speaker"),
            msg("2026-10-06T10:00:01+09:00", "speaker11", "Eleventh speaker"),
        ],
    }

    payload = build_meeting_payload(item, model_id=MODEL)

    assert payload["meeting"]["speakers"] == ["Speaker 3", "Speaker 11"]
    assert [m["speaker"] for m in payload["messages"]] == ["speaker3", "speaker11"]



def test_payload_carries_page_style_and_defaults_to_visual():
    item = {"meetingId": "m1", "messages": [msg("2026-10-06T10:00:00+09:00", "me", "hi")]}
    assert build_meeting_payload(item, model_id=MODEL)["meeting"]["style"] == "visual"
    assert build_meeting_payload(item, model_id=MODEL, style="detailed")["meeting"]["style"] == "detailed"


def test_check_page_style_rejects_unknown_values():
    import pytest
    from app.page_agent import PageRequestError, check_page_style

    assert check_page_style(None) == "visual" and check_page_style("") == "visual"
    assert check_page_style("detailed") == "detailed"
    for bad in ("poster", "brief", 3, ["visual"]):
        with pytest.raises(PageRequestError):
            check_page_style(bad)
