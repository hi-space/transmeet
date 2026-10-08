import json

import pytest

from app.page_agent import PUBLISH_TOOL_NAME, consume_stream, describe_tool_start


async def agen(events):
    for e in events:
        yield e


class Sink:
    def __init__(self):
        self.text, self.status = [], []

    async def on_text(self, t):
        self.text.append(t)

    async def on_status(self, s):
        self.status.append(s)


def text_events(*chunks):
    out = [{"messageStart": {"role": "assistant"}}, {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}}]
    out += [{"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": c}}} for c in chunks]
    out.append({"contentBlockStop": {"contentBlockIndex": 0}})
    return out


async def test_text_deltas_are_relayed_and_joined():
    sink = Sink()
    events = text_events("논의 흐름을 ", "7개 주제로 나눴습니다") + [
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}, "metrics": {"latencyMs": 100}}},
    ]
    out = await consume_stream(agen(events), on_text=sink.on_text, on_status=sink.on_status)
    assert sink.text == ["논의 흐름을 ", "7개 주제로 나눴습니다"]
    assert out.text == "논의 흐름을 7개 주제로 나눴습니다"
    assert out.stop_reason == "end_turn" and out.handoff is None
    assert out.usage["totalTokens"] == 15


async def test_publish_handoff_assembles_chunked_input():
    sink = Sink()
    payload = {"title": "t", "html": "<!doctype html><html lang=\"ko\"></html>"}
    raw = json.dumps(payload)
    events = [
        {"contentBlockStart": {"contentBlockIndex": 1, "start": {"toolUse": {"toolUseId": "tu-1", "name": PUBLISH_TOOL_NAME, "type": "tool_use"}}}},
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": raw[:10]}}}},
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": raw[10:]}}}},
        {"contentBlockStop": {"contentBlockIndex": 1}},
        {"messageStop": {"stopReason": "tool_use"}},
    ]
    out = await consume_stream(agen(events), on_text=sink.on_text, on_status=sink.on_status)
    assert out.stop_reason == "tool_use"
    assert out.handoff is not None and out.handoff.tool_use_id == "tu-1"
    assert out.handoff.arguments() == payload
    assert any("발행" in s for s in sink.status)


async def test_harness_tools_emit_status_but_no_handoff():
    sink = Sink()
    events = [
        {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {"toolUseId": "a", "name": "WebSearch", "type": "mcp_tool_use", "serverName": "web_search"}}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": "{\"query\": \"GitHub OIDC token lifetime\"}"}}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"contentBlockStart": {"contentBlockIndex": 1, "start": {"toolResult": {"toolUseId": "a", "status": "success"}}}},
        {"contentBlockStop": {"contentBlockIndex": 1}},
        {"messageStop": {"stopReason": "tool_result"}},
        {"contentBlockStart": {"contentBlockIndex": 2, "start": {"toolUse": {"toolUseId": "b", "name": "code_interpreter", "type": "server_tool_use"}}}},
        {"contentBlockStop": {"contentBlockIndex": 2}},
        {"messageStop": {"stopReason": "end_turn"}},
    ]
    out = await consume_stream(agen(events), on_text=sink.on_text, on_status=sink.on_status)
    assert out.handoff is None and out.stop_reason == "end_turn"
    assert any("웹 검색" in s and "GitHub OIDC token lifetime" in s for s in sink.status)
    assert any("코드 실행" in s for s in sink.status)


async def test_block_state_resets_between_messages():
    sink = Sink()
    events = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {"toolUseId": "pub-1", "name": PUBLISH_TOOL_NAME, "type": "tool_use"}}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": "{\"title\":\"t\",\"html\":\"<html></html>\"}"}}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "tool_result"}},
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "검색합니다"}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"contentBlockStart": {"contentBlockIndex": 1, "start": {"toolUse": {"toolUseId": "ws-1", "name": "WebSearch", "type": "mcp_tool_use", "serverName": "web_search"}}}},
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": "{\"query\": \"x\"}"}}}},
        {"contentBlockStop": {"contentBlockIndex": 1}},
        {"messageStop": {"stopReason": "tool_use"}},
    ]
    out = await consume_stream(agen(events), on_text=sink.on_text, on_status=sink.on_status)
    assert out.stop_reason == "tool_use"
    assert out.handoff is None  # publish_page 핸드오프는 이전 메시지의 것 — 새어 나오면 안 된다
    assert out.text == "검색합니다"


async def test_handoff_only_counts_when_final_stop_is_tool_use():
    sink = Sink()
    events = [
        {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {"toolUseId": "x", "name": PUBLISH_TOOL_NAME, "type": "tool_use"}}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": "{}"}}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "max_iterations_exceeded"}},
    ]
    out = await consume_stream(agen(events), on_text=sink.on_text, on_status=sink.on_status)
    assert out.stop_reason == "max_iterations_exceeded"
    assert out.handoff is None


async def test_runtime_error_event_raises():
    sink = Sink()
    events = [{"runtimeClientError": {"message": "Failed to fetch skill: AccessDeniedException"}}]
    with pytest.raises(RuntimeError, match="Failed to fetch skill"):
        await consume_stream(agen(events), on_text=sink.on_text, on_status=sink.on_status)


def test_describe_tool_start():
    assert describe_tool_start("WebSearch", "web_search", '{"query":"abc"}') == "웹 검색: abc"
    assert describe_tool_start("code_interpreter", None, "") == "코드 실행 (검증·다이어그램)"
    assert describe_tool_start(PUBLISH_TOOL_NAME, None, "") == "페이지 발행 중"
    assert describe_tool_start("shell", None, "") == "셸 명령 실행"
    assert describe_tool_start("file_operations", None, "") == "파일 작성"
    assert describe_tool_start("other", None, "") == "도구 실행: other"



async def noop(_):
    return None


async def test_usage_is_summed_across_metadata_events():
    events = [
        {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 3, "outputTokens": 2, "totalTokens": 5}}},
    ]
    out = await consume_stream(agen(events), on_text=noop, on_status=noop)
    assert out.usage == {"inputTokens": 13, "outputTokens": 7, "totalTokens": 20}


@pytest.mark.parametrize("key", ["throttlingException", "serviceUnavailableException", "modelStreamErrorException"])
async def test_any_exception_event_raises(key):
    with pytest.raises(RuntimeError, match="slow down"):
        await consume_stream(agen([{key: {"message": "slow down"}}]), on_text=noop, on_status=noop)
