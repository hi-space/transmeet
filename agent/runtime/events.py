"""Progress events streamed to the caller (and relayed by the backend as `page_stream`).

Shape mirrors the backend's existing phases so the frontend needs no change:
status / delta / done / error. `done` and `error` are terminal and appear exactly once.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from langgraph.config import get_stream_writer

logger = logging.getLogger(__name__)

TERMINAL_PHASES = ("done", "error")


def status(text: str) -> Dict[str, Any]:
    return {"phase": "status", "text": text}


def heartbeat() -> Dict[str, Any]:
    """A status without text: keeps the response stream flowing while a model turn is silent.
    The backend relays only statuses with text and the frontend ignores empty ones."""
    return {"phase": "status"}


def delta(text: str) -> Dict[str, Any]:
    return {"phase": "delta", "text": text}


def done(page: Dict[str, Any], usage: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    return {"phase": "done", "page": page, "usage": usage or {}}


def error(message: str, *, transient: bool = False, stage: str = "") -> Dict[str, Any]:
    evt: Dict[str, Any] = {"phase": "error", "error": message, "transient": bool(transient)}
    if stage:
        evt["stage"] = stage
    return evt


def emit(evt: Dict[str, Any]) -> None:
    """Send an event through LangGraph's custom stream; a no-op outside a graph run."""
    try:
        get_stream_writer()(evt)
    except Exception:  # 그래프 밖(단위 테스트·스크립트)에서는 조용히 넘어간다
        logger.debug("[events] no stream writer for %s", evt.get("phase"))
