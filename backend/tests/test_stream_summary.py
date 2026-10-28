"""_stream_summary 전달 경로: 요청한 소켓이 아니라 그 미팅의 *현재* 연결에 보낸다.

Opus 5.5 는 첫 토큰까지 수 초~수십 초 침묵한다. 그 사이 미팅 전환·재접속으로 소켓이 바뀌면
요청한 소켓에 묶인 요약은 닫힌 소켓에 보내다 예외로 죽고 저장도 안 됐다(2026-10-09 운영 로그).
"""
from contextlib import asynccontextmanager

import pytest

from app import ws_handler
from app.ws_handler import ConnectionState


class FakeWS:
    def __init__(self, closed: bool = False):
        self.closed = closed
        self.sent = []

    async def send_json(self, data):
        if self.closed:
            raise RuntimeError("Unexpected ASGI message 'websocket.send', after sending 'websocket.close'")
        self.sent.append(data)


class FakeBedrock:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    async def converse_stream(self, **kwargs):
        self.calls.append(kwargs)

        async def stream():
            for c in self.chunks:
                yield {"contentBlockDelta": {"delta": {"text": c}}}

        return {"stream": stream()}

    async def converse(self, **kwargs):
        return {"output": {"message": {"content": [{"text": "제품 출시 일정 확정"}]}}}


class FakeDDB:
    def __init__(self, title="주간 1:1", summary_style=None, messages=None):
        self.title = title
        self.summary_style = summary_style
        self.messages = messages or []
        # 호출 기록
        self.gets = []
        self.updates = []

    async def query(self, **kwargs):
        assert kwargs["TableName"] == ws_handler.config.MESSAGES_TABLE
        return {"Items": list(self.messages)}

    async def get_item(self, **kwargs):
        self.gets.append(kwargs)
        item = {"title": {"S": self.title}}
        if self.summary_style is not None:
            item["summaryStyle"] = {"S": self.summary_style}
        return {"Item": item}

    async def update_item(self, **kwargs):
        self.updates.append(kwargs)


def install(monkeypatch, bedrock, ddb):
    @asynccontextmanager
    async def fake_bedrock(*_args, **_kwargs):
        yield bedrock

    @asynccontextmanager
    async def fake_ddb():
        yield ddb

    monkeypatch.setattr(ws_handler, "bedrock_client", fake_bedrock)
    monkeypatch.setattr(ws_handler, "dynamodb_client", fake_ddb)
    monkeypatch.setattr(ws_handler, "active_connections", {})
    monkeypatch.setattr(ws_handler, "_ws_send_locks", {})


MESSAGES = [{"speaker": "me", "original": "금요일까지 끝냅니다.", "timestamp": "2026-10-06T01:20:00Z"}]


async def test_summary_reaches_the_meetings_current_socket_when_requester_closed(monkeypatch):
    bedrock, ddb = FakeBedrock(["## 한눈에", " 보기"]), FakeDDB()
    install(monkeypatch, bedrock, ddb)
    old, new, other = FakeWS(closed=True), FakeWS(), FakeWS()
    ws_handler.active_connections.update({
        "old": ConnectionState(ws=old, meeting_id="m1"),
        "new": ConnectionState(ws=new, meeting_id="m1"),
        "other": ConnectionState(ws=other, meeting_id="m2"),
    })

    await ws_handler._stream_summary(ws_handler._meeting_broadcaster("m1"), "m1", "x", inline_messages=MESSAGES)

    deltas = [m["text"] for m in new.sent if m.get("phase") == "delta"]
    assert deltas == ["## 한눈에", " 보기"]
    done = [m for m in new.sent if m.get("phase") == "done"]
    assert done and done[0]["summary"] == "## 한눈에 보기" and done[0]["meetingId"] == "m1"
    assert other.sent == []  # 다른 미팅을 보는 연결에는 보내지 않는다
    # 요청한 소켓이 닫혔어도 요약은 저장된다
    assert ddb.updates and ddb.updates[0]["ExpressionAttributeValues"][":s"] == {"S": "## 한눈에 보기"}


async def test_summary_is_saved_even_with_no_connected_client(monkeypatch):
    bedrock, ddb = FakeBedrock(["요약"]), FakeDDB()
    install(monkeypatch, bedrock, ddb)

    await ws_handler._stream_summary(ws_handler._meeting_broadcaster("m1"), "m1", "x", inline_messages=MESSAGES)

    assert ddb.updates and ddb.updates[0]["ExpressionAttributeValues"][":s"] == {"S": "요약"}


async def test_summary_request_uses_low_effort(monkeypatch):
    """Opus 5.5 는 thinking 을 끌 수 없다. 기본 effort(medium)는 첫 토큰이 2배 늦어 low 로 둔다."""
    bedrock, ddb = FakeBedrock(["요약"]), FakeDDB()
    install(monkeypatch, bedrock, ddb)

    await ws_handler._stream_summary(ws_handler._meeting_broadcaster("m1"), "m1", "x", inline_messages=MESSAGES)

    assert bedrock.calls[0]["additionalModelRequestFields"] == {"output_config": {"effort": "low"}}


@pytest.mark.parametrize(("saved", "requested", "expected", "heading"), [
    (None, None, "detailed", "## 논의 흐름"),
    ("report", None, "report", "## 배경"),
    ("report", "detailed", "detailed", "## 논의 흐름"),
    ("detailed", "report", "report", "## 향후 일정"),
    ("amazon", None, "amazon", "## 고객 요구 (Customer Asks)"),
    ("report", "decisions", "decisions", "## 보류된 결정"),
    ("concise", None, "detailed", "## 논의 흐름"),  # 삭제된 방식은 기본값으로
    ("unknown", None, "detailed", "## 논의 흐름"),
])
async def test_stream_uses_requested_or_saved_style_and_reports_generated_style(
    monkeypatch, saved, requested, expected, heading,
):
    bedrock, ddb = FakeBedrock(["선택한 요약"]), FakeDDB(summary_style=saved)
    install(monkeypatch, bedrock, ddb)
    ws = FakeWS()
    await ws_handler._stream_summary(
        ws.send_json, "m1", "x", inline_messages=MESSAGES, summary_style=requested,
    )
    prompt = bedrock.calls[0]["messages"][0]["content"][0]["text"]
    assert heading in prompt
    assert prompt.endswith("\n---\n금요일까지 끝냅니다.")
    assert all(event["summaryStyle"] == expected for event in ws.sent)
    assert all(event["meetingId"] == "m1" for event in ws.sent)
    update = ddb.updates[0]
    assert "summaryGeneratedStyle" in update["UpdateExpression"]
    assert update["ExpressionAttributeValues"][":style"] == {"S": expected}
    assert "summaryStyle =" not in update["UpdateExpression"]


@pytest.mark.parametrize(("title", "expected_title"), [
    ("2026. 10. 28. 오전 9:05:06", "제품 출시 일정 확정"),
    ("2026. 10. 28. 오후 1:05:06", "제품 출시 일정 확정"),
    ("Meeting 2026. 10. 28. 오후 1:05:06", "제품 출시 일정 확정"),
    ("제품 회의", None),
    ("2026. 10. 28. 오후 1:05:06 제품 회의", None),
])
async def test_summary_generates_titles_only_for_defaults(monkeypatch, title, expected_title):
    bedrock, ddb = FakeBedrock(["요약"]), FakeDDB(title)
    install(monkeypatch, bedrock, ddb)
    ws = FakeWS()
    ws_handler.active_connections["c"] = ConnectionState(ws=ws, meeting_id="m1")

    await ws_handler._stream_summary(
        ws_handler._meeting_broadcaster("m1"), "m1", "x", inline_messages=MESSAGES
    )

    done = next(message for message in ws.sent if message.get("phase") == "done")
    assert done.get("title") == expected_title
    title_updates = [update for update in ddb.updates if update["UpdateExpression"].startswith("SET title")]
    assert len(title_updates) == (1 if expected_title else 0)
    if expected_title:
        assert title_updates[0]["ExpressionAttributeValues"][":t"] == {"S": expected_title}


# ─── Bedrock 일시 장애: 재시도 → 대체 모델 ──────────────────────────────────────
# 2026-10-10 01:43 UTC 운영 로그: us.anthropic.claude-opus-5-5 ConverseStream 이 ServiceUnavailableException
# ("Bedrock is unable to process your request") 을 3~4초 만에 반환. 재시도 1회·폴백 없음이라
# 사용자는 바로 "Summary generation failed" 를 봤다. 같은 시각 global. 프로파일과 Opus 5 는 정상이었다.

from botocore.exceptions import ClientError


def _service_unavailable():
    return ClientError(
        {"Error": {"Code": "ServiceUnavailableException", "Message": "Bedrock is unable to process your request."}},
        "ConverseStream",
    )


class FlakyBedrock(FakeBedrock):
    """앞의 `failures` 개 예외를 순서대로 던진 뒤 정상 스트림을 돌려준다."""

    def __init__(self, chunks, failures):
        super().__init__(chunks)
        self.failures = list(failures)

    async def converse_stream(self, **kwargs):
        if self.failures:
            self.calls.append(kwargs)
            raise self.failures.pop(0)
        return await super().converse_stream(**kwargs)


def _no_backoff(monkeypatch):
    monkeypatch.setattr(ws_handler, "SUMMARY_RETRY_BACKOFF_S", (0, 0))


async def test_transient_bedrock_error_is_retried_on_the_same_model(monkeypatch):
    bedrock, ddb = FlakyBedrock(["요약"], [_service_unavailable()]), FakeDDB()
    install(monkeypatch, bedrock, ddb)
    _no_backoff(monkeypatch)
    ws = FakeWS()
    ws_handler.active_connections["c"] = ConnectionState(ws=ws, meeting_id="m1")

    await ws_handler._stream_summary(ws_handler._meeting_broadcaster("m1"), "m1", "x", inline_messages=MESSAGES)

    assert [c["modelId"] for c in bedrock.calls] == [ws_handler.config.SUMMARY_MODEL_ID] * 2
    assert [m["phase"] for m in ws.sent] == ["delta", "done"]
    assert ddb.updates and ddb.updates[0]["ExpressionAttributeValues"][":s"] == {"S": "요약"}


async def test_primary_model_exhausted_falls_back_to_secondary_model(monkeypatch):
    monkeypatch.setattr(ws_handler.config, "SUMMARY_INVOKE_RETRIES", 1)
    monkeypatch.setattr(ws_handler.config, "SUMMARY_FALLBACK_MODEL_ID", "global.anthropic.claude-opus-5")
    bedrock, ddb = FlakyBedrock(["요약"], [_service_unavailable(), _service_unavailable()]), FakeDDB()
    install(monkeypatch, bedrock, ddb)
    _no_backoff(monkeypatch)
    ws = FakeWS()
    ws_handler.active_connections["c"] = ConnectionState(ws=ws, meeting_id="m1")

    await ws_handler._stream_summary(ws_handler._meeting_broadcaster("m1"), "m1", "x", inline_messages=MESSAGES)

    assert [c["modelId"] for c in bedrock.calls] == [
        ws_handler.config.SUMMARY_MODEL_ID, ws_handler.config.SUMMARY_MODEL_ID, "global.anthropic.claude-opus-5",
    ]
    done = [m for m in ws.sent if m.get("phase") == "done"]
    assert done and done[0]["summary"] == "요약"


async def test_all_models_exhausted_reports_a_transient_error_in_korean(monkeypatch):
    monkeypatch.setattr(ws_handler.config, "SUMMARY_INVOKE_RETRIES", 0)
    monkeypatch.setattr(ws_handler.config, "SUMMARY_FALLBACK_MODEL_ID", "global.anthropic.claude-opus-5")
    bedrock, ddb = FlakyBedrock(["요약"], [_service_unavailable(), _service_unavailable()]), FakeDDB()
    install(monkeypatch, bedrock, ddb)
    _no_backoff(monkeypatch)
    ws = FakeWS()
    ws_handler.active_connections["c"] = ConnectionState(ws=ws, meeting_id="m1")

    await ws_handler._stream_summary(ws_handler._meeting_broadcaster("m1"), "m1", "x", inline_messages=MESSAGES)

    assert len(bedrock.calls) == 2
    assert [m["phase"] for m in ws.sent] == ["error"]
    assert "일시적으로" in ws.sent[0]["error"]
    assert ddb.updates == []


async def test_non_transient_error_is_not_retried(monkeypatch):
    bedrock, ddb = FlakyBedrock(["요약"], [ValueError("bad payload")]), FakeDDB()
    install(monkeypatch, bedrock, ddb)
    _no_backoff(monkeypatch)
    ws = FakeWS()
    ws_handler.active_connections["c"] = ConnectionState(ws=ws, meeting_id="m1")

    await ws_handler._stream_summary(ws_handler._meeting_broadcaster("m1"), "m1", "x", inline_messages=MESSAGES)

    assert len(bedrock.calls) == 1
    assert [m["phase"] for m in ws.sent] == ["error"]
    assert ws.sent[0]["error"] == "Summary generation failed"


async def test_stream_broken_after_partial_text_is_not_retried(monkeypatch):
    """이미 보낸 델타를 되돌릴 수 없으니 중간에 끊긴 스트림은 재시도하지 않는다."""

    class BreaksMidStream(FakeBedrock):
        async def converse_stream(self, **kwargs):
            self.calls.append(kwargs)

            async def stream():
                yield {"contentBlockDelta": {"delta": {"text": "앞부분"}}}
                raise _service_unavailable()

            return {"stream": stream()}

    bedrock, ddb = BreaksMidStream([]), FakeDDB()
    install(monkeypatch, bedrock, ddb)
    _no_backoff(monkeypatch)
    ws = FakeWS()
    ws_handler.active_connections["c"] = ConnectionState(ws=ws, meeting_id="m1")

    await ws_handler._stream_summary(ws_handler._meeting_broadcaster("m1"), "m1", "x", inline_messages=MESSAGES)

    assert len(bedrock.calls) == 1
    assert [m["phase"] for m in ws.sent] == ["delta", "error"]


# ─── 내 양식(커스텀): 회의 레코드의 사본으로만 만든다 ────────────────────────────────

# 사본 테이블(meetingId 키)의 항목. 회의 항목에는 두지 않는다(400 KB 한도).
SNAPSHOT = {"meetingId": {"S": "m1"}, "userId": {"S": "u1"}, "templateId": {"S": "tpl-1"}, "name": {"S": "주간 회의"},
            "kind": {"S": "instruction"}, "body": {"S": "섹션은 이슈 / 원인 / 조치"},
            "templateUpdatedAt": {"S": "2026-10-28T01:00:00Z"}}


class CustomDDB(FakeDDB):
    """회의·사본 테이블만 읽을 수 있다 — 양식 테이블을 읽으면 실패해서, 양식을 지워도 동작함을 고정한다."""

    def __init__(self, custom=None, stored_messages=False, **kwargs):
        if stored_messages:
            # 저장된 대화는 회의 항목이 아니라 메시지 테이블에서 온다
            kwargs["messages"] = [{"id": {"S": "s1"}, "originalText": {"S": "금요일까지 끝냅니다."},
                                   "timestamp": {"S": "2026-10-28T00:01:00+00:00"}}]
        super().__init__(**kwargs)
        self.custom = custom

    async def get_item(self, **kwargs):
        table = kwargs["TableName"]
        assert table in (ws_handler.config.MEETINGS_TABLE, ws_handler.config.SUMMARY_SNAPSHOTS_TABLE), table
        if table == ws_handler.config.SUMMARY_SNAPSHOTS_TABLE:
            self.gets.append(kwargs)
            return {"Item": self.custom} if self.custom is not None else {}
        return await super().get_item(**kwargs)  # 회의 테이블 읽기는 FakeDDB 가 gets 에 기록한다


@pytest.mark.parametrize("inline", [True, False])
async def test_custom_snapshot_drives_prompt_and_label(monkeypatch, inline):
    bedrock = FakeBedrock(["내 양식 요약"])
    ddb = CustomDDB(custom=SNAPSHOT, stored_messages=not inline, summary_style="custom")
    install(monkeypatch, bedrock, ddb)
    ws = FakeWS()
    await ws_handler._stream_summary(
        ws.send_json, "m1", "x", inline_messages=MESSAGES if inline else None, summary_style="custom",
    )
    prompt = bedrock.calls[0]["messages"][0]["content"][0]["text"]
    assert "<사용자_양식>\n섹션은 이슈 / 원인 / 조치\n</사용자_양식>" in prompt
    assert all(e["summaryStyle"] == "custom" and e["summaryLabel"] == "주간 회의" for e in ws.sent)
    assert [g["TableName"] for g in ddb.gets] == [ws_handler.config.MEETINGS_TABLE, ws_handler.config.SUMMARY_SNAPSHOTS_TABLE]
    update = ddb.updates[0]
    assert "summaryGeneratedLabel = :label" in update["UpdateExpression"]
    assert update["ExpressionAttributeValues"][":label"] == {"S": "주간 회의"}
    assert update["ExpressionAttributeValues"][":style"] == {"S": "custom"}


async def test_custom_requested_without_snapshot_generates_detailed_and_removes_label(monkeypatch):
    bedrock, ddb = FakeBedrock(["요약"]), CustomDDB(summary_style="custom")
    install(monkeypatch, bedrock, ddb)
    ws = FakeWS()
    await ws_handler._stream_summary(ws.send_json, "m1", "x", inline_messages=MESSAGES, summary_style="custom")
    assert "## 논의 흐름" in bedrock.calls[0]["messages"][0]["content"][0]["text"]
    assert all(e["summaryStyle"] == "detailed" and "summaryLabel" not in e for e in ws.sent)
    update = ddb.updates[0]
    assert "REMOVE summaryGeneratedLabel" in update["UpdateExpression"]
    assert ":label" not in update["ExpressionAttributeValues"]


async def test_builtin_style_on_a_meeting_with_a_snapshot_ignores_it(monkeypatch):
    bedrock, ddb = FakeBedrock(["요약"]), CustomDDB(custom=SNAPSHOT, summary_style="custom")
    install(monkeypatch, bedrock, ddb)
    ws = FakeWS()
    await ws_handler._stream_summary(ws.send_json, "m1", "x", inline_messages=MESSAGES, summary_style="report")
    prompt = bedrock.calls[0]["messages"][0]["content"][0]["text"]
    assert "<사용자_양식>" not in prompt and "## 배경" in prompt
    assert "REMOVE summaryGeneratedLabel" in ddb.updates[0]["UpdateExpression"]


async def test_builtin_style_does_not_read_the_snapshot_table(monkeypatch):
    bedrock, ddb = FakeBedrock(["요약"]), CustomDDB(custom=SNAPSHOT, summary_style="report")
    install(monkeypatch, bedrock, ddb)
    await ws_handler._stream_summary(FakeWS().send_json, "m1", "x", inline_messages=MESSAGES)
    assert [g["TableName"] for g in ddb.gets] == [ws_handler.config.MEETINGS_TABLE]


async def test_stored_messages_are_read_from_messages_table(monkeypatch):
    stored = [{"id": {"S": "a"}, "originalText": {"S": "출시는 금요일"}, "timestamp": {"S": "2026-10-28T00:01:00+00:00"}}]
    bedrock, ddb = FakeBedrock(["요약"]), FakeDDB(messages=stored)
    install(monkeypatch, bedrock, ddb)

    await ws_handler._stream_summary(ws_handler._meeting_broadcaster("m1"), "m1", "x")

    assert "출시는 금요일" in bedrock.calls[0]["messages"][0]["content"][0]["text"]
    assert ddb.gets[0]["ProjectionExpression"] == "title, summaryStyle"
