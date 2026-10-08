import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from botocore.exceptions import ClientError

from app import page_agent
from app.page_agent import (
    MAX_INLINE_CHARS,
    MAX_INLINE_MESSAGES,
    PUBLISH_TOOL_NAME,
    PageAgentDeps,
    PageRequestError,
    check_inline_messages,
    check_meeting_id,
    is_transient_harness_error,
    page_status_event,
    run_page_generation,
    run_unpublish,
    start_page_generation,
)
from botocore.exceptions import EventStreamError

SECTIONS = ["header", "tldr", "decisions", "action-items", "discussion", "open-questions", "references", "insights", "glossary", "footer"]
GOOD = ('<!doctype html><html lang="ko"><head><meta name="transmeet:meeting-id" content="m1">'
        '<meta name="transmeet:generated-at" content="t"><meta name="transmeet:model" content="x"></head><body>'
        + "".join(f'<section data-section="{s}"><p>x</p></section>' for s in SECTIONS) + "</body></html>")


async def agen(events):
    for e in events:
        yield e


def handoff_events(tool_use_id, args):
    return [
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "페이지를 조립했습니다"}}},
        {"contentBlockStart": {"contentBlockIndex": 1, "start": {"toolUse": {"toolUseId": tool_use_id, "name": PUBLISH_TOOL_NAME, "type": "tool_use"}}}},
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": json.dumps(args)}}}},
        {"contentBlockStop": {"contentBlockIndex": 1}},
        {"messageStop": {"stopReason": "tool_use"}},
    ]


def final_events(text="발행했습니다."):
    return [
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": text}}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}}},
    ]


class FakeAgentCore:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    async def invoke_harness(self, **kw):
        self.calls.append(kw)
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return {"stream": agen(nxt)}


class FakeS3:
    def __init__(self):
        self.objects, self.deleted = {}, []

    async def put_object(self, **kw):
        self.objects[kw["Key"]] = kw

    async def delete_object(self, **kw):
        self.deleted.append(kw["Key"])


class FakeDDB:
    def __init__(self, item):
        self.item, self.updates = (dict(item) if item else None), []

    async def get_item(self, **kw):
        return {"Item": self.item} if self.item else {}

    async def update_item(self, **kw):
        self.updates.append(kw)
        if "attribute_exists" in kw.get("ConditionExpression", "") and not self.item:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "UpdateItem")
        old = dict(self.item or {})
        if self.item is not None and ":p" in kw.get("ExpressionAttributeValues", {}):
            self.item["page"] = kw["ExpressionAttributeValues"][":p"]
        return {"Attributes": old} if kw.get("ReturnValues") == "ALL_OLD" else {}


def deps_for(agentcore, s3, ddb, invoke_retries=2):
    def cm(obj):
        @asynccontextmanager
        async def _cm():
            yield obj
        return _cm
    return PageAgentDeps(agentcore_client=cm(agentcore), s3_client=cm(s3), ddb_client=cm(ddb),
                         harness_arn="arn:aws:bedrock-agentcore:us-east-1:1:harness/h-abc", bucket="pages",
                         base_url="https://x/p", table="t", max_bytes=1_500_000, publish_retries=2,
                         invoke_retries=invoke_retries, retry_backoff=(0, 0))


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


async def test_happy_path_publishes_and_emits_done():
    ac = FakeAgentCore([handoff_events("tu-1", {"title": "t", "html": GOOD, "share_summary": "s"}), final_events()])
    s3, ddb, send = FakeS3(), FakeDDB(ITEM), Collector()
    page = await run_page_generation(send, "m1", deps=deps_for(ac, s3, ddb))
    assert page is not None and len(s3.objects) == 1
    phases = [e["phase"] for e in send.events]
    assert phases[0] == "status" and "delta" in phases and phases[-1] == "done"
    done = send.events[-1]
    assert done["type"] == "page_stream" and done["page"]["url"] == page.url
    # 두 번째 호출은 같은 세션으로 toolResult 를 보낸다
    assert ac.calls[0]["runtimeSessionId"] == ac.calls[1]["runtimeSessionId"]
    assert len(ac.calls[0]["runtimeSessionId"]) >= 33
    tr = ac.calls[1]["messages"][0]["content"][0]["toolResult"]
    assert tr["toolUseId"] == "tu-1" and tr["status"] == "success"
    assert json.loads(tr["content"][0]["text"])["url"] == page.url
    assert ac.calls[1]["tools"][0]["name"] == PUBLISH_TOOL_NAME  # 오버라이드 도구는 다시 보낸다
    assert ac.calls[0]["harnessArn"].endswith("harness/h-abc")


async def test_invalid_html_is_returned_as_tool_error_then_retried():
    bad = {"title": "t", "html": GOOD.replace("<body>", "<body><script>1</script>")}
    ac = FakeAgentCore([handoff_events("tu-1", bad), handoff_events("tu-2", {"title": "t", "html": GOOD}), final_events()])
    s3, ddb, send = FakeS3(), FakeDDB(ITEM), Collector()
    page = await run_page_generation(send, "m1", deps=deps_for(ac, s3, ddb))
    assert page is not None
    first_result = ac.calls[1]["messages"][0]["content"][0]["toolResult"]
    assert first_result["status"] == "error" and "script" in first_result["content"][0]["text"].lower()
    assert send.events[-1]["phase"] == "done"


async def test_retries_exhausted_emits_error():
    bad = {"title": "t", "html": "<html></html>"}
    ac = FakeAgentCore([handoff_events("a", bad), handoff_events("b", bad), handoff_events("c", bad)])
    send = Collector()
    page = await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM)))
    assert page is None
    assert send.events[-1]["phase"] == "error" and "발행" in send.events[-1]["error"]


async def test_end_turn_without_publish_is_error():
    ac = FakeAgentCore([final_events("다 했습니다")])
    send = Collector()
    page = await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM)))
    assert page is None and send.events[-1]["phase"] == "error"


async def test_no_messages_is_immediate_error():
    empty = {**ITEM, "messages": {"L": []}}
    ac = FakeAgentCore([])
    send = Collector()
    assert await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(empty))) is None
    assert send.events[-1]["phase"] == "error" and ac.calls == []


async def test_generate_page_persists_when_ws_send_fails():
    ac = FakeAgentCore([handoff_events("tu-1", {"title": "t", "html": GOOD}), final_events()])
    s3, ddb = FakeS3(), FakeDDB(ITEM)
    page = await run_page_generation(Collector(fail=True), "m1", deps=deps_for(ac, s3, ddb))
    assert page is not None and len(s3.objects) == 1 and ddb.updates


async def test_previous_page_is_removed_on_regeneration():
    item = {**ITEM, "page": {"M": {"pageId": {"S": "old"}, "url": {"S": "https://x/p/m1/old.html"}}}}
    ac = FakeAgentCore([handoff_events("tu-1", {"title": "t", "html": GOOD}), final_events()])
    s3 = FakeS3()
    await run_page_generation(Collector(), "m1", deps=deps_for(ac, s3, FakeDDB(item)))
    assert "p/m1/old.html" in s3.deleted


async def test_run_unpublish():
    item = {**ITEM, "page": {"M": {"pageId": {"S": "old"}}}}
    s3, ddb, send = FakeS3(), FakeDDB(item), Collector()
    await run_unpublish(send, "m1", deps=deps_for(FakeAgentCore([]), s3, ddb))
    assert s3.deleted == ["p/m1/old.html"] and send.events[-1]["phase"] == "unpublished"



# ─── I1: 발행 이후 실패는 done 으로 보고 ──

async def test_followup_invoke_failure_after_publish_still_done():
    ac = FakeAgentCore([handoff_events("tu-1", {"title": "t", "html": GOOD}), RuntimeError("stream dropped")])
    s3, ddb, send = FakeS3(), FakeDDB(ITEM), Collector()
    page = await run_page_generation(send, "m1", deps=deps_for(ac, s3, ddb))
    assert page is not None
    assert send.events[-1]["phase"] == "done" and send.events[-1]["page"]["url"] == page.url
    assert not any(e["phase"] == "error" for e in send.events)


async def test_followup_stream_exception_event_after_publish_still_done():
    ac = FakeAgentCore([handoff_events("tu-1", {"title": "t", "html": GOOD}),
                        [{"serviceUnavailableException": {"message": "busy"}}]])
    send = Collector()
    page = await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM)))
    assert page is not None and send.events[-1]["phase"] == "done"


async def test_followup_timeout_after_publish_still_done():
    ac = FakeAgentCore([handoff_events("tu-1", {"title": "t", "html": GOOD}),
                        [{"messageStop": {"stopReason": "timeout_exceeded"}}]])
    send = Collector()
    page = await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM)))
    assert page is not None and send.events[-1]["phase"] == "done"


async def test_republish_validation_exhausted_after_publish_still_done():
    bad = {"title": "t", "html": "<html></html>"}
    ac = FakeAgentCore([handoff_events("a", {"title": "t", "html": GOOD}), handoff_events("b", bad),
                        handoff_events("c", bad), handoff_events("d", bad)])
    send = Collector()
    page = await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM)))
    assert page is not None and send.events[-1]["phase"] == "done"


async def test_page_stream_events_carry_meeting_id():
    ac = FakeAgentCore([handoff_events("tu-1", {"title": "t", "html": GOOD}), final_events()])
    send = Collector()
    await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM)))
    assert send.events and all(e["meetingId"] == "m1" for e in send.events)


# ─── I3: 없는 미팅·요청 검증 ──

async def test_missing_meeting_errors_before_invoking_harness():
    ac = FakeAgentCore([])
    send = Collector()
    assert await run_page_generation(send, "nope", deps=deps_for(ac, FakeS3(), FakeDDB(None))) is None
    assert send.events[-1] == {"type": "page_stream", "meetingId": "nope", "phase": "error", "error": "미팅을 찾을 수 없습니다"}
    assert ac.calls == []


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


# ─── I2: 같은 미팅 중복 생성 차단 ──

async def test_second_generate_for_same_meeting_is_rejected(monkeypatch):
    release = asyncio.Event()
    started = []

    async def fake_run(send, meeting_id, *, inline_messages=None, deps=None):
        started.append(meeting_id)
        await release.wait()

    monkeypatch.setattr(page_agent, "run_page_generation", fake_run)
    first = start_page_generation(Collector(), "m1")
    await asyncio.sleep(0)
    with pytest.raises(PageRequestError, match="이미 생성 중"):
        start_page_generation(Collector(), "m1")  # 다른 연결이어도 같은 미팅이면 거절
    other = start_page_generation(Collector(), "m2")
    release.set()
    await asyncio.gather(first, other)
    await asyncio.sleep(0)
    assert started == ["m1", "m2"] and "m1" not in page_agent._ACTIVE_PAGE_TASKS
    again = start_page_generation(Collector(), "m1")  # 끝난 뒤에는 다시 가능
    await again


async def test_start_page_generation_validates_request():
    with pytest.raises(PageRequestError):
        start_page_generation(Collector(), "../etc")
    with pytest.raises(PageRequestError):
        start_page_generation(Collector(), "m1", inline_messages=[{"original": "x"}] * (MAX_INLINE_MESSAGES + 1))


async def test_unpublish_events_are_tagged():
    item = {**ITEM, "page": {"M": {"pageId": {"S": "old"}}}}
    send = Collector()
    await run_unpublish(send, "m1", deps=deps_for(FakeAgentCore([]), FakeS3(), FakeDDB(item)))
    assert send.events[-1]["op"] == "unpublish" and send.events[-1]["meetingId"] == "m1"


# ─── 일시적 Bedrock 오류는 새 세션으로 다시 시도 ──

TRANSIENT_MSG = "serviceUnavailableException: Bedrock is unable to process your request"


async def test_transient_invoke_error_restarts_with_new_session():
    ac = FakeAgentCore([RuntimeError(TRANSIENT_MSG), handoff_events("tu-1", {"title": "t", "html": GOOD}), final_events()])
    s3, ddb, send = FakeS3(), FakeDDB(ITEM), Collector()
    page = await run_page_generation(send, "m1", deps=deps_for(ac, s3, ddb))
    assert page is not None and send.events[-1]["phase"] == "done"
    assert len(ac.calls) == 3
    assert ac.calls[0]["runtimeSessionId"] != ac.calls[1]["runtimeSessionId"]
    assert "text" in ac.calls[1]["messages"][0]["content"][0]  # 처음부터 다시 — toolResult 가 아니다
    assert any(e["phase"] == "status" and "다시 시도" in e["text"] for e in send.events)
    assert not any(e["phase"] == "error" for e in send.events)


async def test_transient_retries_exhausted_emits_error():
    ac = FakeAgentCore([RuntimeError(TRANSIENT_MSG)] * 3)
    send = Collector()
    assert await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM))) is None
    assert len(ac.calls) == 3 and send.events[-1]["phase"] == "error"


async def test_non_transient_error_is_not_retried():
    ac = FakeAgentCore([RuntimeError("AccessDeniedException: not allowed")])
    send = Collector()
    assert await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM))) is None
    assert len(ac.calls) == 1 and send.events[-1]["phase"] == "error"


async def test_transient_error_after_publish_is_not_retried():
    ac = FakeAgentCore([handoff_events("tu-1", {"title": "t", "html": GOOD}), RuntimeError(TRANSIENT_MSG)])
    send = Collector()
    page = await run_page_generation(send, "m1", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM)))
    assert page is not None and len(ac.calls) == 2 and send.events[-1]["phase"] == "done"


@pytest.mark.parametrize("exc,expected", [
    (RuntimeError(TRANSIENT_MSG), True),
    (RuntimeError("throttlingException: slow down"), True),
    (EventStreamError({"Error": {"Code": "runtimeClientError", "Message": "An error occurred (ServiceUnavailableException) when calling the ConverseStream operation (reached max retries: 2): Bedrock is unable to process your request."}}, "InvokeHarness"), True),
    (RuntimeError("modelStreamErrorException: stream broke"), True),
    (RuntimeError("validationException: bad input"), False),
    (ValueError("edge references unknown node"), False),
])
def test_is_transient_harness_error(exc, expected):
    assert is_transient_harness_error(exc) is expected


# ─── 재접속한 클라이언트가 놓친 결과를 되묻는다 ──

async def test_page_status_reports_active_generation(monkeypatch):
    page_agent._LAST_PAGE_RESULTS.clear()
    gate = asyncio.Event()

    class SlowAC:
        turns = [handoff_events("tu-1", {"title": "t", "html": GOOD}), final_events()]

        async def invoke_harness(self, **kw):
            await gate.wait()
            return {"stream": agen(self.turns.pop(0))}

    monkeypatch.setattr(PageAgentDeps, "from_config", classmethod(lambda cls: deps_for(SlowAC(), FakeS3(), FakeDDB(ITEM))))
    task = start_page_generation(Collector(), "m-active")
    await asyncio.sleep(0)
    ev = page_status_event("m-active")
    assert ev == {"type": "page_stream", "meetingId": "m-active", "phase": "status"}
    gate.set()
    page = await task
    assert page is not None and page_status_event("m-active")["phase"] == "done"


async def test_page_status_replays_done_and_error():
    page_agent._LAST_PAGE_RESULTS.clear()
    ac = FakeAgentCore([handoff_events("tu-1", {"title": "t", "html": GOOD}), final_events()])
    page = await run_page_generation(Collector(fail=True), "m-done", deps=deps_for(ac, FakeS3(), FakeDDB(ITEM)))
    ev = page_status_event("m-done")
    assert ev["phase"] == "done" and ev["page"]["url"] == page.url and ev["meetingId"] == "m-done"

    await run_page_generation(Collector(fail=True), "m-err", deps=deps_for(FakeAgentCore([RuntimeError("x")]), FakeS3(), FakeDDB(ITEM)))
    ev = page_status_event("m-err")
    assert ev["phase"] == "error" and ev["error"]


def test_page_status_unknown_meeting_is_error():
    page_agent._LAST_PAGE_RESULTS.clear()
    ev = page_status_event("m-unknown")
    assert ev["phase"] == "error" and "중단" in ev["error"]


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
