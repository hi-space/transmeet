"""Bedrock chat model factory + transient-error handling."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Sequence, Tuple

from langchain_aws import ChatBedrockConverse

logger = logging.getLogger(__name__)

PRIMARY_MODEL_ID = "us.anthropic.claude-opus-5-5"
FALLBACK_MODEL_ID = "us.anthropic.claude-opus-5"
ALLOWED_MODEL_IDS: Tuple[str, ...] = (PRIMARY_MODEL_ID, FALLBACK_MODEL_ID)
DEFAULT_MAX_TOKENS = 64000

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
    return ChatBedrockConverse(model_id=model_id, region_name=region, max_tokens=max_tokens, **kwargs)


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
