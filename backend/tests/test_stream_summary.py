"""_stream_summary 전달 경로: 요청한 소켓이 아니라 그 미팅의 *현재* 연결에 보낸다.

Opus 5.5 는 첫 토큰까지 수 초~수십 초 침묵한다. 그 사이 미팅 전환·재접속으로 소켓이 바뀌면
요청한 소켓에 묶인 요약은 닫힌 소켓에 보내다 예외로 죽고 저장도 안 됐다(2026-10-09 운영 로그).
"""
from contextlib import asynccontextmanager

from app import ws_handler
from app.ws_handler import ConnectionState


class FakeWS:
    def __init__(self, closed: bool = False):
        self.closed = closed
        self.sent = []

    async def send_json(self, data):
        if self.closed:
            raise RuntimeError("Unexpected ASGI message 'websocket.send', after sending 'websocket.close'")
        self.sent.append(data)


class FakeBedrock:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    async def converse_stream(self, **kwargs):
        self.calls.append(kwargs)

        async def stream():
            for c in self.chunks:
                yield {"contentBlockDelta": {"delta": {"text": c}}}

        return {"stream": stream()}


class FakeDDB:
    def __init__(self):
        self.updates = []

    async def get_item(self, **kwargs):
        return {"Item": {"title": {"S": "주간 1:1"}}}

    async def update_item(self, **kwargs):
        self.updates.append(kwargs)


def install(monkeypatch, bedrock, ddb):
    @asynccontextmanager
    async def fake_bedrock(*_args, **_kwargs):
        yield bedrock

    @asynccontextmanager
    async def fake_ddb():
        yield ddb

    monkeypatch.setattr(ws_handler, "bedrock_client", fake_bedrock)
    monkeypatch.setattr(ws_handler, "dynamodb_client", fake_ddb)
    monkeypatch.setattr(ws_handler, "active_connections", {})
    monkeypatch.setattr(ws_handler, "_ws_send_locks", {})


MESSAGES = [{"speaker": "me", "original": "금요일까지 끝냅니다.", "timestamp": "2026-10-06T01:20:00Z"}]


async def test_summary_reaches_the_meetings_current_socket_when_requester_closed(monkeypatch):
    bedrock, ddb = FakeBedrock(["## 한눈에", " 보기"]), FakeDDB()
    install(monkeypatch, bedrock, ddb)
    old, new, other = FakeWS(closed=True), FakeWS(), FakeWS()
    ws_handler.active_connections.update({
        "old": ConnectionState(ws=old, meeting_id="m1"),
        "new": ConnectionState(ws=new, meeting_id="m1"),
        "other": ConnectionState(ws=other, meeting_id="m2"),
    })

    await ws_handler._stream_summary(ws_handler._meeting_broadcaster("m1"), "m1", "x", inline_messages=MESSAGES)

    deltas = [m["text"] for m in new.sent if m.get("phase") == "delta"]
    assert deltas == ["## 한눈에", " 보기"]
    done = [m for m in new.sent if m.get("phase") == "done"]
    assert done and done[0]["summary"] == "## 한눈에 보기" and done[0]["meetingId"] == "m1"
    assert other.sent == []  # 다른 미팅을 보는 연결에는 보내지 않는다
    # 요청한 소켓이 닫혔어도 요약은 저장된다
    assert ddb.updates and ddb.updates[0]["ExpressionAttributeValues"][":s"] == {"S": "## 한눈에 보기"}


async def test_summary_is_saved_even_with_no_connected_client(monkeypatch):
    bedrock, ddb = FakeBedrock(["요약"]), FakeDDB()
    install(monkeypatch, bedrock, ddb)

    await ws_handler._stream_summary(ws_handler._meeting_broadcaster("m1"), "m1", "x", inline_messages=MESSAGES)

    assert ddb.updates and ddb.updates[0]["ExpressionAttributeValues"][":s"] == {"S": "요약"}


async def test_summary_request_uses_low_effort(monkeypatch):
    """Opus 5.5 는 thinking 을 끌 수 없다. 기본 effort(medium)는 첫 토큰이 2배 늦어 low 로 둔다."""
    bedrock, ddb = FakeBedrock(["요약"]), FakeDDB()
    install(monkeypatch, bedrock, ddb)

    await ws_handler._stream_summary(ws_handler._meeting_broadcaster("m1"), "m1", "x", inline_messages=MESSAGES)

    assert bedrock.calls[0]["additionalModelRequestFields"] == {"output_config": {"effort": "low"}}
