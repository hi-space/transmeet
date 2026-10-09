"""Bedrock chat model factory + transient-error handling."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional, Sequence, Tuple

from botocore.config import Config
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import AIMessage, AIMessageChunk

logger = logging.getLogger(__name__)

PRIMARY_MODEL_ID = "us.anthropic.claude-opus-5-5"
FALLBACK_MODEL_ID = "us.anthropic.claude-opus-5"
ALLOWED_MODEL_IDS: Tuple[str, ...] = (PRIMARY_MODEL_ID, FALLBACK_MODEL_ID)
DEFAULT_MAX_TOKENS = 64000
# 60K 토큰 입력에 Opus 5.5 가 첫 토큰을 내기까지 수 분이 걸릴 수 있다. botocore 기본 60초(langchain-aws 는 300초)면 끊긴다.
BEDROCK_CLIENT_CONFIG = Config(read_timeout=900, connect_timeout=10, retries={"max_attempts": 2})

# Bedrock 이 일시적으로 거절하거나 연결이 끊긴 경우. 새 시도로 풀릴 수 있는 것만.
TRANSIENT_MARKERS = (
    "serviceunavailable",
    "throttling",
    "modelnotready",
    "internalserver",
    "modelstreamerror",
    "bedrock is unable to process",
    "read timed out",
    "readtimeout",
    "connection reset",
    "connection aborted",
    "too many requests",
    "service unavailable",
    "remoteprotocolerror",
    # langchain-aws: 이벤트 스트림이 messageStop 없이 끝남(연결이 중간에 끊김) — 새 턴으로 다시 시작하면 풀린다
    "incomplete bedrock response stream",
    "missing messagestop",
    "connectionerror",
)


class TransientModelError(Exception):
    """Bedrock kept refusing after retries — the caller may restart with another model."""


def is_transient_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    code = ((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code", "")
    text += " " + str(code).lower()
    return any(m in text for m in TRANSIENT_MARKERS)


def make_chat_model(model_id: str, *, region: str, max_tokens: int = DEFAULT_MAX_TOKENS, **kwargs: Any) -> ChatBedrockConverse:
    if model_id not in ALLOWED_MODEL_IDS:
        raise ValueError(f"model not allowed: {model_id}")
    kwargs.setdefault("config", BEDROCK_CLIENT_CONFIG)
    return ChatBedrockConverse(model_id=model_id, region_name=region, max_tokens=max_tokens, **kwargs)


def _chunk_to_message(acc: Any) -> AIMessage:
    if isinstance(acc, AIMessageChunk):
        return AIMessage(
            content=acc.content, tool_calls=list(acc.tool_calls or []), id=acc.id,
            usage_metadata=acc.usage_metadata, response_metadata=dict(acc.response_metadata or {}),
            additional_kwargs=dict(acc.additional_kwargs or {}),
        )
    return acc


async def astream_with_retry(
    llm,
    messages: Sequence[Any],
    *,
    on_text: Optional[Callable[[str], Awaitable[None] | None]] = None,
    attempts: int = 3,
    backoff: Sequence[float] = (5.0, 15.0),
    **kwargs,
) -> AIMessage:
    """Stream one model turn, accumulating chunks into a single AIMessage.

    Streaming keeps the connection alive while the model thinks (no read timeout on a
    multi-minute first token) and lets the caller relay text deltas as they arrive.
    Transient Bedrock errors restart the whole turn with backoff.
    """
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        acc: Any = None
        try:
            async for chunk in llm.astream(list(messages), **kwargs):
                acc = chunk if acc is None else acc + chunk
                text = getattr(chunk, "text", "")
                if on_text is not None and isinstance(text, str) and text:
                    res = on_text(text)
                    if asyncio.iscoroutine(res):
                        await res
            if acc is None:
                raise RuntimeError("model returned an empty stream")
            return _chunk_to_message(acc)
        except Exception as e:
            if not is_transient_error(e):
                raise
            last = e
            if attempt >= attempts:
                break
            delay = backoff[min(attempt - 1, len(backoff) - 1)] if backoff else 0
            logger.warning("[model] transient stream error attempt %d/%d, retry in %.0fs: %s", attempt, attempts, delay, e)
            if delay:
                await asyncio.sleep(delay)
    raise TransientModelError(str(last)) from last


async def ainvoke_with_retry(llm, messages: Sequence[Any], *, attempts: int = 3, backoff: Sequence[float] = (5.0, 15.0), **kwargs):
    """Call `llm.ainvoke`, retrying transient Bedrock errors with backoff.

    Non-transient errors propagate immediately. When the budget is exhausted a
    TransientModelError is raised so the orchestrator can classify it as retryable.
    """
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await llm.ainvoke(list(messages), **kwargs)
        except Exception as e:
            if not is_transient_error(e):
                raise
            last = e
            if attempt >= attempts:
                break
            delay = backoff[min(attempt - 1, len(backoff) - 1)] if backoff else 0
            logger.warning("[model] transient error attempt %d/%d, retry in %.0fs: %s", attempt, attempts, delay, e)
            if delay:
                await asyncio.sleep(delay)
    raise TransientModelError(str(last)) from last
