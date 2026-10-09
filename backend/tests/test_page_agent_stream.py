import json

from app.page_agent import parse_sse_lines


class Body:
    def __init__(self, lines):
        self.lines = lines

    async def iter_lines(self):
        for l in self.lines:
            yield l


async def collect(lines):
    return [e async for e in parse_sse_lines(Body(lines))]


async def test_parses_data_lines_and_skips_noise():
    lines = [b"", b": keep-alive", b"event: ping", b"data: " + json.dumps({"phase": "status", "text": "a"}).encode(),
             b"", b"data:   " + json.dumps({"phase": "delta", "text": "b"}).encode(), b"data:", b"data: {broken",
             b"data: [1, 2]", "data: " + json.dumps({"phase": "done", "page": {"url": "https://x"}})]
    out = await collect(lines)
    assert out == [{"phase": "status", "text": "a"}, {"phase": "delta", "text": "b"}, {"phase": "done", "page": {"url": "https://x"}}]


async def test_handles_korean_utf8_and_crlf():
    payload = json.dumps({"phase": "status", "text": "논의 흐름을 7개 주제로 나눴습니다"}, ensure_ascii=False).encode("utf-8")
    out = await collect([b"data: " + payload + b"\r"])
    assert out[0]["text"] == "논의 흐름을 7개 주제로 나눴습니다"


async def test_empty_body():
    assert await collect([]) == []
