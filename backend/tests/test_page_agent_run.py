import json
from contextlib import asynccontextmanager

from app.page_agent import PUBLISH_TOOL_NAME, PageAgentDeps, run_page_generation, run_unpublish

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
        return {"stream": agen(self.responses.pop(0))}


class FakeS3:
    def __init__(self):
        self.objects, self.deleted = {}, []

    async def put_object(self, **kw):
        self.objects[kw["Key"]] = kw

    async def delete_object(self, **kw):
        self.deleted.append(kw["Key"])


class FakeDDB:
    def __init__(self, item):
        self.item, self.updates = item, []

    async def get_item(self, **kw):
        return {"Item": self.item} if self.item else {}

    async def update_item(self, **kw):
        self.updates.append(kw)


def deps_for(agentcore, s3, ddb):
    def cm(obj):
        @asynccontextmanager
        async def _cm():
            yield obj
        return _cm
    return PageAgentDeps(agentcore_client=cm(agentcore), s3_client=cm(s3), ddb_client=cm(ddb),
                         harness_arn="arn:aws:bedrock-agentcore:us-east-1:1:harness/h-abc", bucket="pages",
                         base_url="https://x/p", table="t", max_bytes=1_500_000, publish_retries=2)


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
