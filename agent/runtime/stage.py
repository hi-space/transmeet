"""One tool-using stage: model ⇄ tools loop with a hard iteration cap.

Each graph node that needs the model runs through `run_stage`. The loop ends when the
model answers without tool calls (`final`) or when `max_iterations` model calls have been
made (`max_iterations`). Text the model writes is streamed as `delta`; tool starts as
`status` lines in Korean (the app shows them as a progress log).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from . import events
from .model import ainvoke_with_retry

logger = logging.getLogger(__name__)

TOOL_RESULT_LIMIT = 60_000


def cached_text(text: str) -> list:
    """Text block followed by a Bedrock prompt-cache checkpoint.

    The system prompt and the user prompt (transcript + stage inputs) are identical on every
    iteration of the tool loop, so marking them lets Bedrock reuse that prefix at ~10% of the
    input price. Prefixes shorter than the model's minimum are simply not cached.
    """
    return [{"type": "text", "text": text}, {"cachePoint": {"type": "default"}}]


@dataclass
class StageResult:
    messages: List[BaseMessage]
    iterations: int
    usage: Dict[str, int] = field(default_factory=dict)
    stopped: Literal["final", "max_iterations"] = "final"

    @property
    def final_text(self) -> str:
        for m in reversed(self.messages):
            if isinstance(m, AIMessage):
                return message_text(m)
        return ""


def message_text(msg: BaseMessage) -> str:
    text = getattr(msg, "text", "")
    if isinstance(text, str) and text:
        return str(text)
    content = msg.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def describe_tool_start(name: str, args: Dict[str, Any]) -> Optional[str]:
    args = args or {}
    if name == "web_search":
        return f"웹 검색: {str(args.get('query', ''))[:80]}"
    if name == "bash":
        return f"명령 실행: {str(args.get('command', '')).strip().splitlines()[0][:60] if args.get('command') else ''}"
    if name in ("write_file", "edit_file"):
        return f"파일 작성: {args.get('path', '')}"
    if name == "load_skill":
        return f"스킬 로드: {args.get('name', '')}"
    return None


def _add_usage(total: Dict[str, int], msg: BaseMessage) -> None:
    usage = getattr(msg, "usage_metadata", None) or {}
    for k in ("input_tokens", "output_tokens", "total_tokens"):
        if usage.get(k):
            total[k] = total.get(k, 0) + int(usage[k])


async def run_stage(
    *,
    name: str,
    llm,
    system_prompt: str,
    user_prompt: str,
    tools: Sequence[BaseTool],
    max_iterations: int,
    on_tool_start: Optional[Callable[[str, Dict[str, Any]], Optional[str]]] = None,
    stream_text: bool = True,
    cache_prefix: bool = True,
) -> StageResult:
    by_name = {t.name: t for t in tools}
    bound = llm.bind_tools(list(tools)) if tools else llm
    wrap = cached_text if cache_prefix else (lambda t: t)
    messages: List[BaseMessage] = [SystemMessage(content=wrap(system_prompt)), HumanMessage(content=wrap(user_prompt))]
    usage: Dict[str, int] = {}
    describe = on_tool_start or describe_tool_start

    for iteration in range(1, max_iterations + 1):
        ai: AIMessage = await ainvoke_with_retry(bound, messages)
        messages.append(ai)
        _add_usage(usage, ai)
        text = message_text(ai).strip()
        if text and stream_text:
            events.emit(events.delta(text))
        calls = list(getattr(ai, "tool_calls", None) or [])
        if not calls:
            return StageResult(messages, iteration, usage, "final")
        for call in calls:
            tool_name, args, call_id = call.get("name", ""), call.get("args") or {}, call.get("id") or ""
            label = describe(tool_name, args)
            if label:
                events.emit(events.status(label))
            tool = by_name.get(tool_name)
            if tool is None:
                result = f"error: unknown tool '{tool_name}'. available: {', '.join(by_name)}"
            else:
                try:
                    result = await tool.ainvoke(args)
                except Exception as e:  # 도구 오류는 모델이 보고 고치게 한다
                    logger.warning("[stage:%s] tool %s failed: %s", name, tool_name, e)
                    result = f"error: {type(e).__name__}: {e}"
            result_text = result if isinstance(result, str) else str(result)
            if len(result_text) > TOOL_RESULT_LIMIT:
                result_text = result_text[:TOOL_RESULT_LIMIT] + f"\n[... truncated {len(result_text) - TOOL_RESULT_LIMIT} chars ...]"
            messages.append(ToolMessage(content=result_text, tool_call_id=call_id, name=tool_name))
    logger.warning("[stage:%s] reached max iterations (%d)", name, max_iterations)
    return StageResult(messages, max_iterations, usage, "max_iterations")
