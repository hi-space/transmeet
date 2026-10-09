"""AgentCore Runtime entrypoint: POST /invocations → SSE stream of page events.

`run_page_graph` is the pure orchestration (testable with a fake graph); `invoke` wires
real dependencies (Bedrock chat model, Gateway web search, S3/DynamoDB publisher).
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
import uuid
from typing import Any, AsyncIterator, Dict, Optional

import boto3

from . import events
from .graph import GraphDeps, PayloadError, build_graph, initial_state, merge_usage
from .model import TransientModelError, is_transient_error, make_chat_model
from .publish import PublishError, publish_html
from .settings import Settings
from .web_search import WebSearchClient

logger = logging.getLogger("runtime")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

GENERIC_ERROR = "페이지 생성 중 오류가 발생했습니다"
TIMEOUT_ERROR = "시간 초과로 생성을 중단했습니다"
NO_RESULT_ERROR = "에이전트가 결과 없이 끝났습니다"


async def run_page_graph(
    payload: Dict[str, Any],
    *,
    session_id: str,
    deps: GraphDeps,
    budget_s: float,
    graph: Any = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Yield status/delta events and exactly one terminal done/error event."""
    try:
        state = initial_state(payload, session_id, deps)
    except PayloadError as e:
        yield events.error(str(e), transient=False, stage="payload")
        return
    except Exception as e:  # 작업 디렉터리 생성 실패 등
        logger.exception("[run] initial_state failed")
        yield events.error(GENERIC_ERROR, transient=is_transient_error(e), stage="prepare")
        return

    app = graph or build_graph(deps)
    published: Optional[Dict[str, Any]] = None
    usage: Dict[str, int] = {}
    failed = False
    try:
        async with asyncio.timeout(budget_s):
            async for mode, data in app.astream(state, stream_mode=["custom", "updates"]):
                if mode == "custom":
                    if data.get("phase") == "error":
                        failed = True
                    yield data
                    continue
                for _node, update in (data or {}).items():
                    if not isinstance(update, dict):
                        continue
                    if update.get("published"):
                        published = update["published"]
                    if update.get("usage"):
                        usage = merge_usage(usage, update["usage"])
    except TimeoutError:
        logger.error("[run] budget exceeded (%.0fs) session=%s", budget_s, session_id)
        yield events.error(TIMEOUT_ERROR, transient=False, stage="budget")
        return
    except PublishError as e:
        logger.error("[run] publish failed: %s", e)
        yield events.error("페이지 발행에 실패했습니다: " + "; ".join(e.errors[:3]), transient=e.transient, stage="publish")
        return
    except TransientModelError as e:
        logger.warning("[run] model unavailable: %s", e)
        yield events.error("모델이 일시적으로 응답하지 않습니다", transient=True, stage="model")
        return
    except Exception as e:
        logger.exception("[run] graph failed")
        yield events.error(GENERIC_ERROR, transient=is_transient_error(e), stage="graph")
        return

    if usage:
        logger.info("[run] session=%s usage=%s", session_id, usage)
    if published:
        yield events.done(published, usage)
    elif not failed:
        yield events.error(NO_RESULT_ERROR, transient=True, stage="graph")


# ─── Real dependencies ───────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def _clients(region: str):
    return boto3.client("s3", region_name=region), boto3.client("dynamodb", region_name=region)


def build_deps(settings: Settings, web_search: Optional[WebSearchClient]) -> GraphDeps:
    s3, ddb = _clients(settings.region)

    async def publisher(meeting_id: str, html: str, title: str, share_summary: str):
        return await asyncio.to_thread(
            publish_html, meeting_id, html, title, share_summary, s3=s3, ddb=ddb,
            bucket=settings.pages_bucket, base_url=settings.pages_base_url, table=settings.meetings_table,
            pages_table=settings.pages_table, max_bytes=settings.page_max_bytes,
        )

    def llm_factory(model_id: str, stage: str):
        return make_chat_model(model_id, region=settings.region)

    return GraphDeps(
        llm_factory=llm_factory, publisher=publisher, web_search=web_search,
        skills_dir=settings.skills_dir, work_base=settings.work_base, max_bytes=settings.page_max_bytes,
        tz_name=settings.tz_name,
    )


async def generate(payload: Dict[str, Any], *, session_id: str, settings: Optional[Settings] = None) -> AsyncIterator[Dict[str, Any]]:
    settings = settings or Settings.from_env()
    web_search = WebSearchClient(settings.gateway_url, settings.region) if settings.gateway_url else None
    if web_search is None:
        logger.warning("[run] WEB_SEARCH_GATEWAY_URL not set — research stage runs without web search")
    deps = build_deps(settings, web_search)
    try:
        async for evt in run_page_graph(payload, session_id=session_id, deps=deps, budget_s=settings.budget_s):
            yield evt
    finally:
        if web_search is not None:
            await web_search.aclose()


def create_app():
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    app = BedrockAgentCoreApp()

    @app.entrypoint
    async def invoke(payload: Dict[str, Any], context):
        session_id = getattr(context, "session_id", None) or uuid.uuid4().hex
        async for evt in generate(payload, session_id=session_id):
            yield evt

    return app


if __name__ == "__main__":
    create_app().run()
