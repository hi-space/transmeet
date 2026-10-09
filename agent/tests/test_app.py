import asyncio
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from runtime import app as A
from runtime.graph import GraphDeps
from runtime.model import TransientModelError
from runtime.publish import PublishError
from test_graph import PAYLOAD

SKILLS = Path(__file__).resolve().parents[1] / "skills"


class FakeGraph:
    def __init__(self, events, exc=None, delay=0.0):
        self.events, self.exc, self.delay = events, exc, delay

    async def astream(self, state, stream_mode=None):
        for e in self.events:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield e
        if self.exc:
            raise self.exc


def deps(tmp_path):
    async def pub(*a):
        raise AssertionError("not used")
    return GraphDeps(llm_factory=lambda m, s: None, publisher=pub, skills_dir=SKILLS, work_base=tmp_path)


async def collect(graph, tmp_path, payload=PAYLOAD, budget=5.0):
    out = []
    async for e in A.run_page_graph(payload, session_id="s" * 33, deps=deps(tmp_path), budget_s=budget, graph=graph):
        out.append(e)
    return out


async def test_passes_custom_events_and_ends_with_done(tmp_path):
    g = FakeGraph([
        ("custom", {"phase": "status", "text": "a"}),
        ("updates", {"outline": {"usage": {"input_tokens": 10, "output_tokens": 1}}}),
        ("custom", {"phase": "delta", "text": "b"}),
        ("updates", {"assemble": {"usage": {"input_tokens": 5}}}),
        ("updates", {"publish": {"published": {"pageId": "p", "url": "https://x"}}}),
    ])
    out = await collect(g, tmp_path)
    assert [e["phase"] for e in out] == ["status", "delta", "done"]
    assert out[-1]["page"]["url"] == "https://x" and out[-1]["usage"] == {"input_tokens": 15, "output_tokens": 1}


async def test_fail_node_error_is_terminal(tmp_path):
    g = FakeGraph([("custom", {"phase": "error", "error": "x", "transient": False}), ("updates", {"fail": {"failed": True}})])
    out = await collect(g, tmp_path)
    assert [e["phase"] for e in out] == ["error"] and out[0]["error"] == "x"


async def test_no_result_is_transient_error(tmp_path):
    out = await collect(FakeGraph([("updates", {"outline": {}})]), tmp_path)
    assert out[-1]["phase"] == "error" and out[-1]["transient"] is True


@pytest.mark.parametrize("exc,transient", [
    (ClientError({"Error": {"Code": "ServiceUnavailableException", "Message": "x"}}, "Converse"), True),
    (TransientModelError("ServiceUnavailableException"), True),
    (ClientError({"Error": {"Code": "ValidationException", "Message": "x"}}, "Converse"), False),
    (RuntimeError("boom"), False),
])
async def test_exceptions_become_error_events(tmp_path, exc, transient):
    out = await collect(FakeGraph([("custom", {"phase": "status", "text": "a"})], exc=exc), tmp_path)
    assert out[-1]["phase"] == "error" and out[-1]["transient"] is transient and len(out) == 2


async def test_publish_error_message_includes_reasons(tmp_path):
    out = await collect(FakeGraph([], exc=PublishError(["meeting not found"])), tmp_path)
    assert out[-1]["phase"] == "error" and "meeting not found" in out[-1]["error"] and out[-1]["transient"] is False


async def test_budget_timeout(tmp_path):
    g = FakeGraph([("custom", {"phase": "status", "text": "a"})] * 5, delay=0.2)
    out = await collect(g, tmp_path, budget=0.3)
    assert out[-1]["phase"] == "error" and out[-1]["error"] == A.TIMEOUT_ERROR and len(out) <= 3


async def test_bad_payload_is_non_transient_error(tmp_path):
    out = await collect(FakeGraph([]), tmp_path, payload={"meeting": {"meetingId": "m"}, "messages": []})
    assert out == [{"phase": "error", "error": "기록이 없습니다", "transient": False, "stage": "payload"}]


def test_settings_from_env(monkeypatch):
    from runtime.settings import Settings
    monkeypatch.setenv("PAGES_BUCKET", "b")
    monkeypatch.setenv("WEB_SEARCH_GATEWAY_URL", "https://gw/mcp")
    monkeypatch.setenv("GRAPH_BUDGET_S", "120")
    s = Settings.from_env()
    assert s.pages_bucket == "b" and s.gateway_url == "https://gw/mcp" and s.budget_s == 120.0 and s.region == "us-east-1"
