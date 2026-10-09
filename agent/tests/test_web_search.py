import json

import httpx
import pytest
from botocore.credentials import Credentials

from runtime.web_search import GATEWAY_TOOL_NAME, SigV4Auth, WebSearchClient, make_web_search_tool


def test_sigv4_adds_authorization_headers_and_ignores_transport_headers():
    auth = SigV4Auth("bedrock-agentcore", "us-east-1", credentials=Credentials("AKID", "SECRET"))
    req = httpx.Request("POST", "https://gw.example.com/mcp", json={"jsonrpc": "2.0"},
                        headers={"Host": "gw.example.com", "Content-Length": "999", "Accept": "application/json"})
    flow = auth.auth_flow(req)
    signed = next(flow)
    assert signed.headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKID/")
    assert "bedrock-agentcore/aws4_request" in signed.headers["Authorization"]
    assert "X-Amz-Date" in signed.headers
    signed_headers = signed.headers["Authorization"].split("SignedHeaders=")[1].split(",")[0]
    assert "content-length" not in signed_headers and "host" in signed_headers  # botocore 가 host 는 스스로 넣는다


class Recorder:
    def __init__(self, result="{}", fail=None):
        self.calls, self.result, self.fail = [], result, fail

    async def __call__(self, name, args):
        self.calls.append((name, args))
        if self.fail:
            raise self.fail
        return self.result


async def test_search_maps_arguments_to_gateway_tool():
    rec = Recorder(result={"results": [{"title": "t", "url": "https://docs.aws.amazon.com/x"}]})
    c = WebSearchClient("https://gw/mcp", "us-east-1", call_tool=rec)
    out = await c.search("  AgentCore Runtime quotas  ", max_results=3, include_domains=["docs.aws.amazon.com", " "])
    name, args = rec.calls[0]
    assert name == GATEWAY_TOOL_NAME
    assert args == {"query": "AgentCore Runtime quotas", "maxResults": 3,
                    "filters": {"domainFilter": {"include": ["docs.aws.amazon.com"]}}}
    assert json.loads(out)["results"][0]["url"].startswith("https://docs.aws")


async def test_search_clamps_results_and_query_length():
    rec = Recorder()
    c = WebSearchClient("https://gw/mcp", "us-east-1", call_tool=rec)
    await c.search("q" * 500, max_results=50)
    assert rec.calls[0][1]["maxResults"] == 10 and len(rec.calls[0][1]["query"]) == 200
    assert "filters" not in rec.calls[0][1]


async def test_search_enforces_call_limit():
    rec = Recorder()
    c = WebSearchClient("https://gw/mcp", "us-east-1", call_tool=rec, max_calls=2)
    assert "error" not in await c.search("a")
    assert "error" not in await c.search("b")
    out = await c.search("c")
    assert "limit reached (2)" in out and len(rec.calls) == 2


async def test_search_failure_returns_error_string():
    c = WebSearchClient("https://gw/mcp", "us-east-1", call_tool=Recorder(fail=RuntimeError("boom")))
    assert (await c.search("a")).startswith("error:")
    assert (await c.search("")).startswith("error:")


async def test_tool_schema_and_async_invoke():
    rec = Recorder(result="ok")
    tool = make_web_search_tool(WebSearchClient("https://gw/mcp", "us-east-1", call_tool=rec))
    assert tool.name == "web_search"
    assert tool.args_schema.model_json_schema()["required"] == ["query"]
    assert await tool.ainvoke({"query": "x"}) == "ok"
    with pytest.raises(RuntimeError):
        tool.invoke({"query": "x"})
