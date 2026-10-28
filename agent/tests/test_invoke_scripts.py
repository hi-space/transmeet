"""운영 스크립트(invoke_local·invoke_batch)가 대화를 메시지 테이블에서 읽는지. AWS 호출 없이 가짜 클라이언트."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import invoke_batch  # noqa: E402
import invoke_local  # noqa: E402


def _row(i, speaker="speaker1"):
    ts = f"2026-10-28T00:00:{i * 20:02d}+00:00"
    return {
        "meetingId": {"S": "mtg"},
        "sk": {"S": f"{ts}#m{i}"},
        "id": {"S": f"m{i}"},
        "speaker": {"S": speaker},
        "originalText": {"S": f"hello {i}"},
        "translatedText": {"S": f"안녕 {i}"},
        "detectedLanguage": {"S": "en"},
        "timestamp": {"S": ts},
    }


class FakeDdb:
    """회의 항목에는 messages 가 없고, 대화는 메시지 테이블에만 있다. 쿼리는 2건씩 나눠 준다."""

    def __init__(self):
        self.meeting = {"meetingId": {"S": "mtg"}, "title": {"S": "주간 회의"},
                        "createdAt": {"S": "2026-10-28T00:00:00+00:00"}, "messageCount": {"N": "3"}}
        self.rows = [_row(0), _row(1, "speaker2"), _row(2)]
        self.queries = []

    def get_item(self, **kw):
        assert kw["TableName"] == "transmeet-meetings"
        return {"Item": self.meeting}

    def scan(self, **kw):
        assert kw["TableName"] == "transmeet-meetings"
        return {"Items": [self.meeting]}

    def query(self, **kw):
        self.queries.append(kw)
        assert kw["TableName"] == "transmeet-messages"
        assert kw["ConsistentRead"] is True
        assert kw["ExpressionAttributeValues"] == {":m": {"S": "mtg"}}
        start = 0
        if "ExclusiveStartKey" in kw:
            start = [r["sk"] for r in self.rows].index(kw["ExclusiveStartKey"]["sk"]) + 1
        chunk = self.rows[start:start + 2]
        resp = {"Items": chunk}
        if start + 2 < len(self.rows):
            resp["LastEvaluatedKey"] = {"meetingId": {"S": "mtg"}, "sk": chunk[-1]["sk"]}
        return resp


@pytest.fixture
def ddb(monkeypatch):
    fake = FakeDdb()
    monkeypatch.setattr(invoke_local.boto3, "client", lambda *a, **kw: fake)
    return fake


def test_payload_from_meeting_reads_messages_table(ddb):
    payload = invoke_local.payload_from_meeting("mtg")
    assert [m["original"] for m in payload["messages"]] == ["hello 0", "hello 1", "hello 2"]
    assert set(payload["messages"][0]) == {"timestamp", "speaker", "original", "translation", "detectedLanguage"}
    assert payload["meeting"]["meetingId"] == "mtg"
    assert payload["meeting"]["speakers"] == ["Speaker 1", "Speaker 2"]
    assert len(ddb.queries) == 2  # 페이지를 끝까지 따라간다


def test_load_messages_sorted_by_sort_key(ddb):
    ddb.rows = [ddb.rows[2], ddb.rows[0], ddb.rows[1]]
    assert [m["id"] for m in invoke_local.load_messages("mtg")] == ["m0", "m1", "m2"]


def test_batch_list_meetings_attaches_table_messages(ddb):
    (item,) = invoke_batch.list_meetings()
    assert [m["id"] for m in item["messages"]] == ["m0", "m1", "m2"]
    payload = invoke_local.build_payload(item, item["messages"], "model")
    assert len(payload["messages"]) == 3


def test_payload_from_sample_puts_meeting_without_messages(ddb, tmp_path):
    puts = []
    ddb.put_item = lambda **kw: puts.append(kw)
    sample = tmp_path / "s.json"
    sample.write_text(
        '{"meeting": {"meetingId": "sample-1", "title": "샘플", "startedAt": "2026-10-28T00:00:00+00:00"},'
        ' "messages": [{"timestamp": "2026-10-28T00:00:01+00:00", "speaker": "speaker1", "originalText": "hi"}]}',
        encoding="utf-8",
    )
    payload = invoke_local.payload_from_sample(sample)
    assert [m["original"] for m in payload["messages"]] == ["hi"]
    (put,) = puts
    assert put["TableName"] == invoke_local.MEETINGS_TABLE
    # 대화는 메시지 테이블에 있으므로 회의 항목에 빈 messages 를 두지 않는다
    assert "messages" not in put["Item"]
