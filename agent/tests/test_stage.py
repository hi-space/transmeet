import pytest
from botocore.exceptions import ClientError, EventStreamError, ReadTimeoutError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from fakes import FakeChatModel, ai, tool_call
from runtime import tools as rtools
from runtime.model import TransientModelError, ainvoke_with_retry, astream_with_retry, is_transient_error
from runtime.stage import describe_tool_start, run_stage
from runtime.workspace import create_workspace


@pytest.fixture
def ws(tmp_path):
    return create_workspace("s" * 33, base=tmp_path)


async def test_stage_runs_tools_until_final(ws):
    llm = FakeChatModel(responses=[
        ai("파일을 씁니다", [tool_call("write_file", {"path": "a.txt", "content": "hi"}, "t1")], usage={"in": 10, "out": 2}),
        ai("", [tool_call("nope", {}, "t2"), tool_call("read_file", {"path": "a.txt"}, "t3")], usage={"in": 20, "out": 3, "cached": 15}),
        ai("끝", usage={"in": 5, "out": 1}),
    ])
    res = await run_stage(name="t", llm=llm, system_prompt="sys", user_prompt="user",
                          tools=rtools.make_tools(ws, ["write_file", "read_file"]), max_iterations=5)
    assert res.stopped == "final" and res.iterations == 3 and res.final_text == "끝"
    assert res.usage == {"input_tokens": 35, "output_tokens": 6, "total_tokens": 41, "cache_read": 15}
    assert (ws.root / "a.txt").read_text() == "hi"
    msgs = res.messages
    assert isinstance(msgs[0], SystemMessage) and isinstance(msgs[1], HumanMessage)
    # 고정 접두(시스템·사용자 프롬프트)에는 Bedrock 캐시 포인트가 붙는다
    assert msgs[0].content[0]["text"] == "sys" and msgs[0].content[-1] == {"cachePoint": {"type": "default"}}
    assert msgs[1].content[0]["text"] == "user" and msgs[1].content[-1] == {"cachePoint": {"type": "default"}}
    tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in tool_msgs] == ["t1", "t2", "t3"]
    assert "wrote" in tool_msgs[0].content
    assert "unknown tool 'nope'" in tool_msgs[1].content and "write_file" in tool_msgs[1].content
    assert tool_msgs[2].content == "hi"
    # 모델에 보낸 두 번째 요청에는 첫 도구 결과가 들어 있어야 한다
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "t1" for m in llm.calls[1])
    assert {t.name for t in llm.bound_tools} == {"write_file", "read_file"}


async def test_stage_stops_at_max_iterations(ws):
    llm = FakeChatModel(responses=[ai("", [tool_call("read_file", {"path": "x"}, f"t{i}")]) for i in range(3)])
    res = await run_stage(name="t", llm=llm, system_prompt="s", user_prompt="u",
                          tools=rtools.make_tools(ws, ["read_file"]), max_iterations=2)
    assert res.stopped == "max_iterations" and res.iterations == 2 and len(llm.responses) == 1


async def test_stage_truncates_huge_tool_results(ws):
    (ws.root / "big.txt").write_text("x" * 200_000)
    llm = FakeChatModel(responses=[ai("", [tool_call("read_file", {"path": "big.txt"}, "t1")]), ai("ok")])
    res = await run_stage(name="t", llm=llm, system_prompt="s", user_prompt="u",
                          tools=rtools.make_tools(ws, ["read_file"]), max_iterations=3)
    tm = [m for m in res.messages if isinstance(m, ToolMessage)][0]
    assert len(tm.content) < 61_000 and "truncated" in tm.content


async def test_stage_without_tools_is_single_call():
    llm = FakeChatModel(responses=[ai("답")])
    res = await run_stage(name="t", llm=llm, system_prompt="s", user_prompt="u", tools=[], max_iterations=3)
    assert res.final_text == "답" and res.iterations == 1


def test_describe_tool_start():
    assert describe_tool_start("web_search", {"query": "AgentCore quotas"}) == "웹 검색: AgentCore quotas"
    assert describe_tool_start("bash", {"command": "python3 x.py\necho"}) == "명령 실행: python3 x.py"
    assert describe_tool_start("write_file", {"path": "page.html"}) == "파일 작성: page.html"
    assert describe_tool_start("load_skill", {"name": "meeting-research"}) == "스킬 로드: meeting-research"
    assert describe_tool_start("read_file", {"path": "x"}) is None


def _client_error(code, msg="x"):
    return ClientError({"Error": {"Code": code, "Message": msg}}, "Converse")


@pytest.mark.parametrize("exc,expected", [
    (_client_error("ServiceUnavailableException", "Bedrock is unable to process your request"), True),
    (_client_error("ThrottlingException", "Too many requests"), True),
    (_client_error("ModelNotReadyException"), True),
    (EventStreamError({"Error": {"Code": "modelStreamErrorException", "Message": "x"}}, "ConverseStream"), True),
    (ReadTimeoutError(endpoint_url="https://bedrock"), True),
    (ConnectionResetError("Connection reset by peer"), True),
    (ConnectionError("Incomplete Bedrock response stream: missing messageStop event."), True),  # langchain-aws, 스트림 중간 끊김
    (_client_error("ValidationException", "bad input"), False),
    (_client_error("AccessDeniedException"), False),
    (ValueError("page.html missing"), False),
])
def test_is_transient_error(exc, expected):
    assert is_transient_error(exc) is expected


class Flaky:
    def __init__(self, failures):
        self.failures, self.calls = list(failures), 0

    async def ainvoke(self, messages, **kw):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return AIMessage(content="ok")


async def test_retry_recovers_from_transient_errors():
    llm = Flaky([_client_error("ServiceUnavailableException"), _client_error("ThrottlingException")])
    out = await ainvoke_with_retry(llm, [HumanMessage(content="x")], attempts=3, backoff=(0, 0))
    assert out.content == "ok" and llm.calls == 3


async def test_retry_gives_up_with_transient_model_error():
    llm = Flaky([_client_error("ServiceUnavailableException")] * 3)
    with pytest.raises(TransientModelError):
        await ainvoke_with_retry(llm, [HumanMessage(content="x")], attempts=3, backoff=(0, 0))
    assert llm.calls == 3
    assert is_transient_error(TransientModelError("ServiceUnavailableException: x"))


async def test_retry_does_not_retry_non_transient():
    llm = Flaky([_client_error("ValidationException")])
    with pytest.raises(ClientError):
        await ainvoke_with_retry(llm, [HumanMessage(content="x")], attempts=3, backoff=(0, 0))
    assert llm.calls == 1


class FlakyStream:
    """astream 이 처음 n 번은 중간에 끊기고 그 뒤에는 텍스트 + 도구 호출 청크를 낸다."""

    def __init__(self, failures):
        self.failures, self.calls = list(failures), 0

    async def astream(self, messages, **kw):
        from langchain_core.messages import AIMessageChunk
        self.calls += 1
        yield AIMessageChunk(content="안녕")
        if self.failures:
            raise self.failures.pop(0)
        yield AIMessageChunk(content=" 세상", tool_call_chunks=[{"name": "write_file", "args": '{"path": "a"', "id": "t1", "index": 0, "type": "tool_call_chunk"}])
        yield AIMessageChunk(content="", tool_call_chunks=[{"name": None, "args": ', "content": "x"}', "id": None, "index": 0, "type": "tool_call_chunk"}],
                             usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5})


async def test_astream_accumulates_text_tool_calls_and_usage():
    seen = []
    llm = FlakyStream([])
    msg = await astream_with_retry(llm, [HumanMessage(content="x")], on_text=seen.append, backoff=(0, 0))
    assert isinstance(msg, AIMessage) and type(msg).__name__ == "AIMessage"
    assert msg.text == "안녕 세상" and seen == ["안녕", " 세상"]
    assert msg.tool_calls == [{"name": "write_file", "args": {"path": "a", "content": "x"}, "id": "t1", "type": "tool_call"}]
    assert msg.usage_metadata["total_tokens"] == 5


async def test_astream_retries_broken_stream_then_gives_up():
    llm = FlakyStream([ReadTimeoutError(endpoint_url="https://bedrock")])
    msg = await astream_with_retry(llm, [HumanMessage(content="x")], attempts=3, backoff=(0, 0))
    assert msg.text == "안녕 세상" and llm.calls == 2
    llm = FlakyStream([_client_error("ServiceUnavailableException")] * 3)
    with pytest.raises(TransientModelError):
        await astream_with_retry(llm, [HumanMessage(content="x")], attempts=3, backoff=(0, 0))
    llm = FlakyStream([_client_error("ValidationException")])
    with pytest.raises(ClientError):
        await astream_with_retry(llm, [HumanMessage(content="x")], attempts=3, backoff=(0, 0))
    assert llm.calls == 1


def test_make_chat_model_uses_long_read_timeout():
    from runtime.model import BEDROCK_CLIENT_CONFIG, make_chat_model
    llm = make_chat_model("us.anthropic.claude-opus-5-5", region="us-east-1")
    assert llm.config is BEDROCK_CLIENT_CONFIG and BEDROCK_CLIENT_CONFIG.read_timeout == 900
    with pytest.raises(ValueError):
        make_chat_model("anthropic.claude-haiku", region="us-east-1")
