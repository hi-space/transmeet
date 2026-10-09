"""Scripted chat model for graph/stage tests (no network)."""
from __future__ import annotations

from typing import Any, List, Optional, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda


def tool_call(name: str, args: dict, call_id: str) -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def ai(text: str = "", calls: Optional[List[dict]] = None, usage: Optional[dict] = None) -> AIMessage:
    msg = AIMessage(content=text, tool_calls=calls or [])
    if usage:
        msg.usage_metadata = {"input_tokens": usage.get("in", 0), "output_tokens": usage.get("out", 0),
                              "total_tokens": usage.get("in", 0) + usage.get("out", 0),
                              "input_token_details": {"cache_read": usage.get("cached", 0), "cache_creation": 0}}
    return msg


class FakeChatModel(BaseChatModel):
    """Returns scripted AIMessages in order; raises scripted exceptions in place."""

    responses: List[Any] = []
    structured: List[Any] = []
    calls: List[List[BaseMessage]] = []
    bound_tools: List[Any] = []

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None,
                  run_manager: Optional[CallbackManagerForLLMRun] = None, **kwargs: Any) -> ChatResult:
        self.calls.append(list(messages))
        if not self.responses:
            raise AssertionError("FakeChatModel ran out of scripted responses")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return ChatResult(generations=[ChatGeneration(message=nxt)])

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any):
        self.bound_tools = list(tools)
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any):
        def _run(_input: Any) -> Any:
            self.calls.append(_input if isinstance(_input, list) else [_input])
            if not self.structured:
                raise AssertionError("FakeChatModel ran out of structured responses")
            nxt = self.structured.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        return RunnableLambda(_run)
