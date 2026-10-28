"""Exercise copy actions with real browser clipboards and mocked meeting data.

Run against a dev server with NEXT_PUBLIC_API_ENDPOINT=<base>/__ui_api,
NEXT_PUBLIC_WS_ENDPOINT= and empty Cognito settings:
TRANSMEET_TEST_URL=http://127.0.0.1:3177 python tests/clipboard_browser.py
"""

import asyncio
import json
import os
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

BASE = os.environ.get("TRANSMEET_TEST_URL", "http://127.0.0.1:3177").rstrip("/")
SUMMARY = """## 결정 사항
- **다음 주 출시** 확정
- [출시 문서](https://example.com/release) 확인

> 결제 오류부터 해결

| 담당 | 작업 |
| --- | --- |
| 민수 | **결제 검증** |

```js
const release = true;
```
"""
PAGE_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>body {color: red}</style></head>
<body><main><h1>제품 출시 회의</h1><nav class="toc">목차는 복사하지 않음</nav>
<h2>결정 사항</h2><ul><li><strong>다음 주 출시</strong> 확정</li></ul>
<p><a href="https://example.com/release">출시 문서</a> 확인</p>
<table><thead><tr><th>담당</th><th>작업</th></tr></thead>
<tbody><tr><td>민수</td><td><strong>결제 검증</strong></td></tr></tbody></table>
<figure><svg><title>출시 흐름</title><desc>검증 후 출시</desc></svg></figure>
<p>Before<br>After</p><p>Type **literal** and _draft_ exactly.</p>
<p>Write [x](y) literally.</p><p><code>echo `date`</code></p>
<pre><code>```md
example
```
</code></pre>
<blockquote><span class="spk">Alice</span>Translation<span class="orig">Original</span></blockquote>
</main></body></html>"""
MEETING = {
    "meetingId": "copy-meeting",
    "title": "제품 출시 회의",
    "createdAt": "2026-10-28T10:00:00Z",
    "messageCount": 3,
    "messages": [
        {"id": "voice-1", "speaker": "speaker1", "originalText": "다음 주에 출시합니다.",
         "translatedText": "We launch next week.", "timestamp": "2026-10-28T10:01:00Z"},
        {"id": "voice-2", "speaker": "speaker3", "originalText": "결제 검증을 맡겠습니다.",
         "translatedText": "I will verify payments.", "timestamp": "2026-10-28T10:02:00Z"},
        {"id": "note-1", "speaker": "me", "originalText": "별도 메모입니다.",
         "translatedText": "", "timestamp": "2026-10-28T10:03:00Z"},
    ],
    "summary": SUMMARY,
    "page": {
        "pageId": "copy-page", "url": BASE + "/p/copy-page/",
        "generatedAt": "2026-10-28T10:04:00Z", "title": "제품 출시 회의",
    },
}


async def clipboard(page):
    return await page.evaluate("""async () => {
      const items = await navigator.clipboard.read();
      const result = {};
      for (const item of items) for (const type of item.types)
        result[type] = await (await item.getType(type)).text();
      return result;
    }""")


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            permissions=["clipboard-read", "clipboard-write"],
        )
        await context.add_init_script("""
          if (window === window.top) {
          localStorage.setItem('transmeet-settings', JSON.stringify({
            autoSummarizeMessageCount: 0, ttsAutoPlay: false
          }));
          const NativeWebSocket = window.WebSocket;
          window.WebSocket = class {
            static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
            readyState = 0;
            constructor(url, protocols) {
              if (!url.includes('/__ui_ws')) return new NativeWebSocket(url, protocols);
              setTimeout(() => { this.readyState = 1; this.onopen?.({}); }, 0);
            }
            send() {}
            close() { this.readyState = 3; this.onclose?.({code: 1000}); }
          };
          }
        """)
        page = await context.new_page()
        errors = []
        requests = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def route_api(route):
            method = route.request.method
            path = urlparse(route.request.url).path.removeprefix("/__ui_api")
            requests.append(f"{method} {path}")
            if method == "GET" and path == "/meetings":
                result = [{k: v for k, v in MEETING.items() if k != "messages"}]
            elif method == "GET" and path == "/me/summary-templates":
                result = {"templates": []}
            elif method == "GET" and path == "/meetings/copy-meeting":
                result = MEETING
            elif method == "POST" and path == "/meetings":
                result = {"meetingId": "empty", "title": "빈 회의", "createdAt": MEETING["createdAt"],
                          "messageCount": 0, "messages": []}
            elif method == "DELETE":
                await route.fulfill(status=204)
                return
            else:
                raise AssertionError(f"Unexpected API request: {method} {path}")
            await route.fulfill(content_type="application/json", body=json.dumps(result))

        await page.route("**/__ui_api/**", route_api)
        await page.route("**/p/copy-page/", lambda route: route.fulfill(
            content_type="text/html", body=PAGE_HTML))
        try:
            await page.goto(BASE + "/?meeting=copy-meeting", wait_until="domcontentloaded")
            await page.get_by_role("navigation", name="회의 기록").get_by_text(
                MEETING["title"], exact=True).click()
            voice = page.get_by_role("tabpanel", name="대화", exact=True)
            summary = page.get_by_role("tabpanel", name="요약", exact=True)
            await expect(voice).to_contain_text("다음 주에 출시합니다.")
            await expect(voice.get_by_role("button", name="대화 복사", exact=True)).to_be_visible()
            await expect(summary.get_by_role("button", name="대화 복사", exact=True)).to_have_count(0)
            await page.screenshot(path="/tmp/transmeet-copy-desktop.png")
            await voice.get_by_role("button", name="대화 복사", exact=True).click()
            await expect(voice.get_by_role("button", name="복사됨", exact=True)).to_be_visible()
            transcript = (await clipboard(page))["text/plain"]
            assert "제품 출시 회의" in transcript and "다음 주에 출시합니다." in transcript
            assert "결제 검증을 맡겠습니다." in transcript and "별도 메모입니다." not in transcript

            await summary.get_by_role("button", name="요약 복사", exact=True).click()
            await expect(summary.get_by_role("button", name="복사됨", exact=True)).to_be_visible()
            copied = await clipboard(page)
            assert "text/html" in copied, "Formatted copy must include HTML, not only Markdown"
            assert "<strong>다음 주 출시</strong>" in copied["text/html"]
            assert "<strong>결정 사항</strong>" in copied["text/html"]
            assert 'href="https://example.com/release"' in copied["text/html"]
            assert "<table" not in copied["text/html"], "Flatten unsupported Slack tables"
            assert "민수" in copied["text/html"] and "결제 검증" in copied["text/html"]
            assert "##" not in copied["text/plain"] and "**" not in copied["text/plain"]
            assert "const release = true;" in copied["text/plain"]
            await summary.get_by_role("button", name="마크다운 복사", exact=True).click()
            await expect(summary.get_by_role("button", name="복사됨", exact=True)).to_have_count(2)
            assert (await clipboard(page))["text/plain"] == SUMMARY.strip()

            await page.get_by_role("tablist", name="회의 도구").get_by_role(
                "tab", name="회의 페이지", exact=True).click()
            meeting_page = page.get_by_role("tabpanel", name="회의 페이지", exact=True)
            await meeting_page.get_by_role("button", name="서식 복사", exact=True).click()
            await expect(meeting_page.get_by_role("button", name="복사됨", exact=True)).to_be_visible()
            copied = await clipboard(page)
            assert "<strong>다음 주 출시</strong>" in copied["text/html"]
            assert "출시 흐름" in copied["text/plain"]
            assert "목차는 복사하지 않음" not in copied["text/plain"]
            assert "<style" not in copied["text/html"] and "<svg" not in copied["text/html"]
            assert "Type **literal** and _draft_ exactly." in copied["text/plain"]
            assert "Write [x](y) literally." in copied["text/plain"]
            assert "echo `date`" in copied["text/plain"]
            assert "```md\nexample\n```" in copied["text/plain"]
            assert "Before<br" in copied["text/html"]
            assert "Translation<br" in copied["text/html"]
            await meeting_page.get_by_role("button", name="마크다운 복사", exact=True).click()
            await expect(meeting_page.get_by_role("button", name="복사됨", exact=True)).to_have_count(2)
            assert "## 결정 사항" in (await clipboard(page))["text/plain"]
            await expect(meeting_page.get_by_role("button", name="서식 복사", exact=True)).to_be_visible()
            await expect(meeting_page.get_by_role("button", name="마크다운 복사", exact=True)).to_be_visible()
            await page.set_viewport_size({"width": 390, "height": 844})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            await page.screenshot(path="/tmp/transmeet-copy-page-mobile.png")
            await page.set_viewport_size({"width": 1280, "height": 900})

            # A rejected modern API must still preserve HTML through the legacy copy event.
            await page.evaluate("""() => {
              navigator.clipboard.write = async () => { throw new Error('Clipboard blocked') };
            }""")
            await expect(meeting_page.get_by_role("button", name="서식 복사", exact=True)).to_be_visible()
            await meeting_page.get_by_role("button", name="서식 복사", exact=True).click()
            await expect(meeting_page.get_by_role("button", name="복사됨", exact=True)).to_be_visible()
            assert "text/html" in await clipboard(page)

            # A text-only fallback must say that formatting could not be copied.
            await page.evaluate("document.execCommand = () => false")
            await expect(meeting_page.get_by_role("button", name="서식 복사", exact=True)).to_be_visible()
            await meeting_page.get_by_role("button", name="서식 복사", exact=True).click()
            await expect(meeting_page.get_by_role("button", name="텍스트 복사됨", exact=True)).to_be_visible()
            copied = await clipboard(page)
            assert "text/html" not in copied and "다음 주 출시" in copied["text/plain"]

            # Clipboard denial must show a failure rather than claiming a successful copy.
            await page.evaluate("""() => {
              document.execCommand = () => false;
              navigator.clipboard.writeText = async () => { throw new Error('Clipboard blocked') };
            }""")
            await expect(meeting_page.get_by_role("button", name="서식 복사", exact=True)).to_be_visible()
            await meeting_page.get_by_role("button", name="서식 복사", exact=True).click()
            await expect(meeting_page.get_by_role("button", name="복사 실패", exact=True)).to_be_visible()

            await page.set_viewport_size({"width": 390, "height": 844})
            await page.get_by_role("tablist", name="회의 화면").get_by_role(
                "tab", name="대화", exact=True).click()
            await expect(voice.get_by_role("button", name="대화 복사", exact=True)).to_be_visible()
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            await page.screenshot(path="/tmp/transmeet-copy-mobile.png")
            await page.set_viewport_size({"width": 320, "height": 740})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert await voice.locator(".ui-panel-header").evaluate(
                "el => el.scrollWidth <= el.clientWidth")
            MEETING["summary"] = "```\n    x = 1\n```"
            await page.set_viewport_size({"width": 1280, "height": 900})
            await page.reload(wait_until="domcontentloaded")
            await page.get_by_role("navigation", name="회의 기록").get_by_text(
                MEETING["title"], exact=True).click()
            await summary.get_by_role("button", name="요약 복사", exact=True).click()
            await expect(summary.get_by_role("button", name="복사됨", exact=True)).to_be_visible()
            assert (await clipboard(page))["text/plain"] == "    x = 1"
            assert not errors, errors
            print(json.dumps({"passed": True, "runtimeErrors": errors}))
        except Exception:
            print(json.dumps({
                "requests": requests, "runtimeErrors": errors,
                "body": (await page.locator("body").inner_text())[:2000],
            }, ensure_ascii=False))
            raise
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
