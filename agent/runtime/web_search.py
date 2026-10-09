"""`web_search` tool backed by the AgentCore Gateway Web Search connector (MCP over HTTPS, SigV4).

The Gateway exposes one MCP tool, `web-search-tool___WebSearch`. We keep a single MCP
session per graph run (opened lazily on the first call) and cap the number of searches so
a runaway research stage cannot burn the budget.
"""
from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional

import boto3
import httpx
from botocore.auth import SigV4Auth as _BotoSigV4
from botocore.awsrequest import AWSRequest
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

GATEWAY_TOOL_NAME = "web-search-tool___WebSearch"
DEFAULT_MAX_CALLS = 12
# 서명에서 빼는 헤더 — 전송 계층이 다시 쓰거나(host, content-length) 서명 뒤에 바뀔 수 있는 것들
_UNSIGNED_HEADERS = {"connection", "host", "content-length", "accept-encoding", "user-agent", "transfer-encoding"}


class SigV4Auth(httpx.Auth):
    requires_request_body = True

    def __init__(self, service: str, region: str, *, session: Optional[boto3.Session] = None, credentials=None):
        self.service = service
        self.region = region
        self._session = session
        self._credentials = credentials

    def _creds(self):
        if self._credentials is not None:
            return self._credentials
        session = self._session or boto3.Session()
        creds = session.get_credentials()
        if creds is None:
            raise RuntimeError("no AWS credentials available for Gateway SigV4")
        return creds.get_frozen_credentials()

    def auth_flow(self, request: httpx.Request):
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _UNSIGNED_HEADERS}
        aws_req = AWSRequest(method=request.method, url=str(request.url), data=request.content, headers=headers)
        _BotoSigV4(self._creds(), self.service, self.region).add_auth(aws_req)
        for k, v in aws_req.headers.items():
            request.headers[k] = v
        yield request


class WebSearchClient:
    def __init__(self, gateway_url: str, region: str, *, max_calls: int = DEFAULT_MAX_CALLS, call_tool=None,
                 timeout_s: float = 60.0):
        self.gateway_url = gateway_url
        self.region = region
        self.max_calls = max_calls
        self.calls = 0
        self._call_tool = call_tool  # 테스트 주입용: async (name, args) -> result
        self._stack: Optional[AsyncExitStack] = None
        self._session = None
        self._timeout_s = timeout_s

    async def _ensure_session(self):
        if self._call_tool is not None or self._session is not None:
            return
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        stack = AsyncExitStack()
        http = await stack.enter_async_context(
            httpx.AsyncClient(auth=SigV4Auth("bedrock-agentcore", self.region), timeout=self._timeout_s)
        )
        read, write = await stack.enter_async_context(streamable_http_client(self.gateway_url, http_client=http))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._stack, self._session = stack, session

    async def _invoke(self, args: Dict[str, Any]):
        if self._call_tool is not None:
            return await self._call_tool(GATEWAY_TOOL_NAME, args)
        await self._ensure_session()
        res = await self._session.call_tool(GATEWAY_TOOL_NAME, args)
        parts = [getattr(c, "text", None) for c in (res.content or [])]
        text = "\n".join(p for p in parts if p)
        if getattr(res, "isError", False) or getattr(res, "is_error", False):
            raise RuntimeError(text or "gateway returned an error")
        return text

    @staticmethod
    def build_args(query: str, max_results: int = 5, include_domains: Optional[List[str]] = None) -> Dict[str, Any]:
        args: Dict[str, Any] = {"query": query.strip()[:200], "maxResults": max(1, min(int(max_results), 10))}
        if include_domains:
            args["filters"] = {"domainFilter": {"include": [d.strip() for d in include_domains if d.strip()][:20]}}
        return args

    async def search(self, query: str, max_results: int = 5, include_domains: Optional[List[str]] = None) -> str:
        if not query or not query.strip():
            return "error: query is required"
        if self.calls >= self.max_calls:
            return f"error: web_search limit reached ({self.max_calls}); use what you already have"
        self.calls += 1
        try:
            result = await self._invoke(self.build_args(query, max_results, include_domains))
        except Exception as e:  # 도구는 예외를 던지지 않는다 — 모델이 다음 행동을 고르게 한다
            logger.warning("[web_search] failed: %s", e)
            return f"error: web search failed: {e}"
        if isinstance(result, (dict, list)):
            return json.dumps(result, ensure_ascii=False)
        return str(result)

    async def aclose(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:
                logger.debug("[web_search] close failed", exc_info=True)
            self._stack, self._session = None, None


class WebSearchArgs(BaseModel):
    query: str = Field(description="검색어. 200자 이내, 영어 권장(공식 문서는 대부분 영어)")
    max_results: int = Field(default=5, ge=1, le=10, description="결과 수 (1~10)")
    include_domains: Optional[List[str]] = Field(default=None, description="이 도메인들로만 좁힌다 (예: [\"docs.aws.amazon.com\"])")


def make_web_search_tool(client: WebSearchClient) -> BaseTool:
    async def _run(query: str, max_results: int = 5, include_domains: Optional[List[str]] = None) -> str:
        return await client.search(query, max_results, include_domains)

    def _sync(*a, **k):  # 그래프는 항상 비동기로 호출한다
        raise RuntimeError("web_search is async-only")

    return StructuredTool.from_function(
        func=_sync, coroutine=_run, name="web_search", args_schema=WebSearchArgs,
        description="웹을 검색해 공식 출처를 찾는다(AgentCore Gateway Web Search). 결과는 제목·URL·본문 발췌 JSON. 한 실행에서 최대 12회.",
    )
