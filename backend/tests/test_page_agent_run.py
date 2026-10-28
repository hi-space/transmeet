import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from botocore.exceptions import ClientError

from app import page_agent
from app.page_agent import (
    MAX_INLINE_CHARS,
    MAX_INLINE_MESSAGES,
    PageAgentDeps,
    PageRequestError,
    check_inline_messages,
    check_meeting_id,
    is_transient_agent_error,
    new_session_id,
    page_status_event,
    run_page_generation,
    run_unpublish,
    start_page_generation,
)

RUNTIME = "arn:aws:bedrock-agentcore:us-east-1:1:runtime/transmeet_meeting_page-abc"
PAGE = {"pageId": "pid", "url": "https://x/p/m1/pid.html", "generatedAt": "2099-01-01T00:00:00+00:00", "title": "t", "shareSummary": "s"}


def sse(*events):
    """에이전트가 보내는 SSE 바이트 라인 목록 (data: JSON 한 줄 + 빈 줄)."""
    out = []
    for e in events:
        out.append(("data: " + json.dumps(e, ensure_ascii=False)).encode("utf-8"))
        out.append(b"")
    return out


def ok_stream(page=PAGE):
    return sse({"phase": "status", "text": "미팅 기록을 준비합니다"}, {"phase": "delta", "text": "조립 중"},
               {"phase": "status", "text": "페이지가 발행되었습니다"}, {"phase": "done", "page": page, "usage": {"input_tokens": 1}})


class FakeBody:
    def __init__(self, lines):
        self.lines = list(lines)

    async def iter_lines(self):
        for l in self.lines:
            yield l


class FakeAgentCore:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    async def invoke_agent_runtime(self, **kw):
        self.calls.append(kw)
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return {"response": FakeBody(nxt), "contentType": "text/event-stream", "statusCode": 200}


class FakeS3:
    def __init__(self):
        self.objects, self.deleted = {}, []

    async def delete_object(self, **kw):
        self.deleted.append(kw["Key"])


class FakeDDB:
    """미팅 테이블 't' 에 항목 하나, 페이지 테이블 'p' 에 (있으면) 페이지 기록 하나."""

    def __init__(self, item, page=None):
        self.item, self.page, self.deletes = (dict(item) if item else None), (dict(page) if page else None), []

    async def get_item(self, **kw):
        if kw["TableName"] == "p":
            return {"Item": self.page} if self.page else {}
        return {"Item": self.item} if self.item else {}

    async def delete_item(self, **kw):
        self.deletes.append(kw)
        if kw["TableName"] == "p":
            self.page = None
        return {}

    async def update_item(self, **kw):
        raise AssertionError("backend must not write to the meeting item")


def deps_for(agentcore, s3, ddb, invoke_retries=2, fallback_model_id="global.anthropic.claude-opus-5"):
    def cm(obj):
        @asynccontextmanager
        async def _cm():
            yield obj
        return _cm
    return PageAgentDeps(agentcore_client=cm(agentcore), s3_client=cm(s3), ddb_client=cm(ddb), runtime_arn=RUNTIME,
                         bucket="pages", table="t", pages_table="p", invoke_retries=invoke_retries, retry_backoff=(0, 0),
                         fallback_model_id=fallback_model_id)


ITEM = {"meetingId": {"S": "m1"}, "title": {"S": "t"}, "createdAt": {"S": "2026-10-06T10:00:00+09:00"},
        "messages": {"L": [{"M": {"speaker": {"S": "speaker1"}, "originalText": {"S": "Hello"}, "translatedText": {"S": "안녕"},
                                   "timestamp": {"S": "2026-10-06T10:00:10+09:00"}}}]}}


class Collector:
    def __init__(self, fail=False):
        self.events, self.fail = [], fail

    async def __call__(self, data):
        if self.fail:
            raise RuntimeError("socket closed")
        self.events.append(data)


def sent_payload(call):
    return json.loads(call["payload"].decode("utf-8"))


async def test_happy_path_relays_events_and_emits_done():
    ac = FakeAgentCore([ok_stream()])
    send = Collector()
    page = await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM)))
    assert page == PAGE
    phases = [e["phase"] for e in send.events]
    assert phases[0] == "status" and "delta" in phases and phases[-1] == "done"
    assert send.events[0].get("started") is True
    assert send.events[-1]["page"]["url"] == PAGE["url"] and all(e["meetingId"] == "m1" for e in send.events)
    assert any(e["phase"] == "status" and e["text"] == "미팅 기록을 준비합니다" for e in send.events)
    call = ac.calls[0]
    assert call["agentRuntimeArn"] == RUNTIME and len(call["runtimeSessionId"]) >= 33
    assert call["contentType"] == "application/json" and call["accept"] == "text/event-stream"
    body = sent_payload(call)
    assert body["meeting"]["meetingId"] == "m1" and body["meeting"]["modelId"] == "global.anthropic.claude-opus-5-5"
    assert body["messages"][0]["original"] == "Hello" and body["messages"][0]["translation"] == "안녕"


async def test_agent_error_is_relayed_and_not_retried_when_not_transient():
    ac = FakeAgentCore([sse({"phase": "status", "text": "x"}, {"phase": "error", "error": "페이지 발행에 실패했습니다: script", "transient": False})])
    send = Collector()
    assert await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM))) is None
    assert len(ac.calls) == 1
    assert send.events[-1]["phase"] == "error" and "script" in send.events[-1]["error"]


async def test_transient_agent_error_restarts_with_new_session():
    ac = FakeAgentCore([sse({"phase": "error", "error": "모델이 일시적으로 응답하지 않습니다", "transient": True}), ok_stream()])
    send = Collector()
    page = await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM)))
    assert page is not None and send.events[-1]["phase"] == "done"
    assert len(ac.calls) == 2 and ac.calls[0]["runtimeSessionId"] != ac.calls[1]["runtimeSessionId"]
    assert any(e["phase"] == "status" and "다시 시도" in e["text"] for e in send.events)
    assert not any(e["phase"] == "error" for e in send.events)


async def test_invoke_exception_transient_is_retried_then_fallback_model():
    boom = ClientError({"Error": {"Code": "ServiceUnavailableException", "Message": "Bedrock is unable to process your request"}}, "InvokeAgentRuntime")
    ac = FakeAgentCore([boom, boom, boom, ok_stream()])
    send = Collector()
    page = await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM)))
    assert page is not None and send.events[-1]["phase"] == "done"
    models = [sent_payload(c)["meeting"]["modelId"] for c in ac.calls]
    assert models == ["global.anthropic.claude-opus-5-5"] * 3 + ["global.anthropic.claude-opus-5"]
    assert any(e["phase"] == "status" and "대체 모델" in e["text"] for e in send.events)


async def test_fallback_gets_its_own_budget_then_error():
    ac = FakeAgentCore([RuntimeError("ServiceUnavailableException: busy")] * 6)
    send = Collector()
    assert await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM))) is None
    assert len(ac.calls) == 6 and send.events[-1]["phase"] == "error"


async def test_no_fallback_configured_fails_after_primary_retries():
    ac = FakeAgentCore([RuntimeError("ServiceUnavailableException: busy")] * 6)
    send = Collector()
    assert await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM), fallback_model_id="")) is None
    assert len(ac.calls) == 3


async def test_non_transient_exception_is_not_retried():
    ac = FakeAgentCore([ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "InvokeAgentRuntime")])
    send = Collector()
    assert await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM))) is None
    assert len(ac.calls) == 1 and send.events[-1]["phase"] == "error"


async def test_stream_ended_without_terminal_is_retried():
    ac = FakeAgentCore([sse({"phase": "status", "text": "a"}), ok_stream()])
    send = Collector()
    page = await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM)))
    assert page is not None and len(ac.calls) == 2


async def test_stream_broken_after_publish_reports_done_from_dynamodb():
    # 에이전트가 발행한 뒤 스트림이 끊겼다 — DynamoDB 에 새 page 가 있으면 다시 만들지 않는다
    fresh = {"meetingId": {"S": "m1"}, "pageId": {"S": "pid"}, "url": {"S": PAGE["url"]}, "generatedAt": {"S": "2099-01-01T00:00:00+00:00"},
             "title": {"S": "t"}, "shareSummary": {"S": "s"}, "updatedAt": {"S": "2099-01-01T00:00:00+00:00"}}
    ac = FakeAgentCore([sse({"phase": "status", "text": "페이지를 발행합니다"})])
    send = Collector()
    page = await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM, page=fresh)))
    assert page["url"] == PAGE["url"] and send.events[-1]["phase"] == "done" and len(ac.calls) == 1
    assert "meetingId" not in page and "updatedAt" not in page and page["title"] == "t"


async def test_stale_page_in_dynamodb_does_not_count_as_published():
    old = {"meetingId": {"S": "m1"}, "pageId": {"S": "old"}, "url": {"S": "https://x/old"}, "generatedAt": {"S": "2020-01-01T00:00:00+00:00"}}
    ac = FakeAgentCore([sse({"phase": "status", "text": "a"}), ok_stream()])
    send = Collector()
    page = await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM, page=old)))
    assert page == PAGE and len(ac.calls) == 2


async def test_malformed_sse_lines_are_skipped():
    lines = [b"data: not json", b": comment", b"event: ping", b""] + ok_stream()
    ac = FakeAgentCore([lines])
    send = Collector()
    assert (await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM)))) == PAGE


async def test_no_messages_is_immediate_error():
    empty = {**ITEM, "messages": {"L": []}}
    ac = FakeAgentCore([])
    send = Collector()
    assert await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(empty))) is None
    assert send.events[-1]["phase"] == "error" and ac.calls == []


async def test_missing_meeting_errors_before_invoking_runtime():
    ac = FakeAgentCore([])
    send = Collector()
    assert await run_page_generation(send, "nope", deps=deps_for(ac, FakeS3(), FakeDDB(None))) is None
    assert send.events[-1] == {"type": "page_stream", "meetingId": "nope", "phase": "error", "error": "미팅을 찾을 수 없습니다"}
    assert ac.calls == []


async def test_generate_page_completes_when_ws_send_fails():
    ac = FakeAgentCore([ok_stream()])
    page = await run_page_generation(Collector(fail=True), "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM)))
    assert page == PAGE


async def test_inline_messages_override_stored_ones():
    ac = FakeAgentCore([ok_stream()])
    inline = [{"speaker": "me", "original": "인라인", "translation": "inline", "timestamp": "2026-10-06T10:01:00+09:00"}]
    await run_page_generation(Collector(), "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM)), inline_messages=inline)
    body = sent_payload(ac.calls[0])
    assert [m["original"] for m in body["messages"]] == ["인라인"]


def test_new_session_id_is_long_enough_and_unique():
    a, b = new_session_id(), new_session_id()
    assert len(a) >= 33 and a != b


@pytest.mark.parametrize("obj,expected", [
    ({"phase": "error", "error": "x", "transient": True}, True),
    ({"phase": "error", "error": "ServiceUnavailableException: busy", "transient": False}, True),
    ({"phase": "error", "error": "페이지 발행에 실패했습니다", "transient": False}, False),
    (RuntimeError("throttlingException: slow down"), True),
    (ClientError({"Error": {"Code": "RuntimeClientError", "Message": "x"}}, "InvokeAgentRuntime"), True),
    (RuntimeError("Read timed out."), True),
    (RuntimeError("validationException: bad input"), False),
    (ValueError("edge references unknown node"), False),
])
def test_is_transient_agent_error(obj, expected):
    assert is_transient_agent_error(obj) is expected


# ─── 요청 검증 ──

def test_meeting_id_must_match_pattern():
    check_meeting_id("m_1-AB")
    check_meeting_id("a" * 64)
    for bad in ["", "a" * 65, "../x", "m 1", "m1\n", None, 5]:
        with pytest.raises(PageRequestError):
            check_meeting_id(bad)


def test_inline_message_caps():
    check_inline_messages(None)
    check_inline_messages([{"original": "x" * 100}] * MAX_INLINE_MESSAGES)
    with pytest.raises(PageRequestError, match="너무 많"):
        check_inline_messages([{"original": "x"}] * (MAX_INLINE_MESSAGES + 1))
    with pytest.raises(PageRequestError, match="너무 깁"):
        check_inline_messages([{"original": "x" * (MAX_INLINE_CHARS // 2), "translation": "y" * (MAX_INLINE_CHARS // 2 + 1)}])
    with pytest.raises(PageRequestError):
        check_inline_messages("not a list")
    with pytest.raises(PageRequestError):
        check_inline_messages(["str"])


# ─── 같은 미팅 중복 생성 차단 ──

async def test_second_generate_for_same_meeting_is_rejected(monkeypatch):
    release = asyncio.Event()
    started = []

    async def fake_run(send, meeting_id, *, inline_messages=None, deps=None, reuse_existing=False,
                       prefer_stored_messages=False):
        started.append(meeting_id)
        await release.wait()

    monkeypatch.setattr(page_agent, "run_page_generation", fake_run)
    first = start_page_generation(Collector(), "m1")
    await asyncio.sleep(0)
    with pytest.raises(PageRequestError, match="이미 생성 중"):
        start_page_generation(Collector(), "m1")
    other = start_page_generation(Collector(), "m2")
    release.set()
    await asyncio.gather(first, other)
    await asyncio.sleep(0)
    assert started == ["m1", "m2"] and "m1" not in page_agent._ACTIVE_PAGE_TASKS
    await start_page_generation(Collector(), "m1")


async def test_start_page_generation_validates_request():
    with pytest.raises(PageRequestError):
        start_page_generation(Collector(), "../etc")
    with pytest.raises(PageRequestError):
        start_page_generation(Collector(), "m1", inline_messages=[{"original": "x"}] * (MAX_INLINE_MESSAGES + 1))


# ─── 공유 해제 ──

OLD_PAGE = {"meetingId": {"S": "m1"}, "pageId": {"S": "old"}, "url": {"S": "https://x/p/m1/old.html"}}


async def test_run_unpublish():
    s3, ddb, send = FakeS3(), FakeDDB(ITEM, page=OLD_PAGE), Collector()
    await run_unpublish(send, "m1", deps=deps_for(FakeAgentCore([]), s3, ddb))
    assert s3.deleted == ["p/m1/old.html"] and send.events[-1]["phase"] == "unpublished"
    assert ddb.deletes == [{"TableName": "p", "Key": {"meetingId": {"S": "m1"}}}] and ddb.page is None
    assert send.events[-1]["op"] == "unpublish" and send.events[-1]["meetingId"] == "m1"


async def test_unpublish_without_page_is_noop_success():
    s3, ddb, send = FakeS3(), FakeDDB(ITEM), Collector()
    await run_unpublish(send, "m1", deps=deps_for(FakeAgentCore([]), s3, ddb))
    assert s3.deleted == [] and send.events[-1]["phase"] == "unpublished"


async def test_unpublish_keeps_field_when_delete_fails():
    class FailingS3(FakeS3):
        async def delete_object(self, **kw):
            raise RuntimeError("boom")

    ddb, send = FakeDDB(ITEM, page=OLD_PAGE), Collector()
    await run_unpublish(send, "m1", deps=deps_for(FakeAgentCore([]), FailingS3(), ddb))
    assert ddb.deletes == [] and ddb.page is not None and send.events[-1]["phase"] == "error"


# ─── 재접속한 클라이언트가 놓친 결과를 되묻는다 ──

async def test_page_status_reports_active_generation(monkeypatch):
    page_agent._LAST_PAGE_RESULTS.clear()
    gate = asyncio.Event()

    class SlowAC:
        async def invoke_agent_runtime(self, **kw):
            await gate.wait()
            return {"response": FakeBody(ok_stream())}

    monkeypatch.setattr(PageAgentDeps, "from_config", classmethod(lambda cls: deps_for(SlowAC(), FakeS3(), FakeDDB(ITEM))))
    task = start_page_generation(Collector(), "m-active")
    await asyncio.sleep(0)
    assert page_status_event("m-active") == {
        "type": "page_stream", "meetingId": "m-active", "phase": "status", "started": True,
    }
    gate.set()
    page = await task
    assert page is not None and page_status_event("m-active")["phase"] == "done"


async def test_page_status_replays_done_and_error():
    page_agent._LAST_PAGE_RESULTS.clear()
    page = await run_page_generation(Collector(fail=True), "m-done", deps=deps_for(FakeAgentCore([ok_stream()]), FakeS3(), FakeDDB(ITEM)))
    ev = page_status_event("m-done")
    assert ev["phase"] == "done" and ev["page"]["url"] == page["url"] and ev["meetingId"] == "m-done"

    await run_page_generation(Collector(fail=True), "m-err", deps=deps_for(FakeAgentCore([RuntimeError("x")]), FakeS3(), FakeDDB(ITEM)))
    ev = page_status_event("m-err")
    assert ev["phase"] == "error" and ev["error"]


def test_page_status_unknown_meeting_is_error():
    page_agent._LAST_PAGE_RESULTS.clear()
    ev = page_status_event("m-unknown")
    assert ev["phase"] == "error" and "중단" in ev["error"]


def test_page_discovery_without_generation_does_not_report_error():
    page_agent._LAST_PAGE_RESULTS.clear()
    assert page_status_event("m-unknown", discover=True) is None


async def test_page_status_forgets_old_results(monkeypatch):
    page_agent._LAST_PAGE_RESULTS.clear()
    now = [1000.0]
    monkeypatch.setattr(page_agent.time, "monotonic", lambda: now[0])
    await run_page_generation(Collector(), "m-old", deps=deps_for(FakeAgentCore([RuntimeError("x")]), FakeS3(), FakeDDB(ITEM)))
    assert page_status_event("m-old")["error"] != page_status_event("m-unknown")["error"]
    now[0] += page_agent.PAGE_RESULT_TTL_S + 1
    assert page_status_event("m-old")["error"] == page_status_event("m-unknown")["error"]


async def test_unpublish_results_are_not_remembered_as_page_status():
    page_agent._LAST_PAGE_RESULTS.clear()
    item = {**ITEM, "page": {"M": {"pageId": {"S": "old"}}}}
    await run_unpublish(Collector(), "m-unpub", deps=deps_for(FakeAgentCore([]), FakeS3(), FakeDDB(item)))
    assert page_status_event("m-unpub")["phase"] == "error"


async def test_discovery_does_not_restore_unpublished_page():
    page_agent._LAST_PAGE_RESULTS.clear()
    deps = deps_for(FakeAgentCore([ok_stream()]), FakeS3(), FakeDDB(ITEM, page=OLD_PAGE))
    await run_page_generation(Collector(), "m1", deps=deps)
    assert page_status_event("m1", discover=True)["phase"] == "done"

    await run_unpublish(Collector(), "m1", deps=deps)

    assert page_status_event("m1", discover=True) is None
