"""Verify brief refreshes independently of auto-summary using mocked API/WebSocket traffic.

Run against a dev server configured with /__ui_api and /__ui_ws, for example:
TRANSMEET_TEST_URL=http://127.0.0.1:3176 python tests/meeting_brief_browser.py
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

BASE = os.environ.get("TRANSMEET_TEST_URL", "http://127.0.0.1:3176").rstrip("/")
TITLE = "직접 정한 제품 출시 회의"
BRIEF = "다음 주 출시를 목표로 결제 오류를 우선 해결하고 담당별 검증 일정을 확정함"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
        await context.add_init_script("""
          localStorage.setItem('transmeet-settings', JSON.stringify({
            autoSummarizeMessageCount: 0, ttsAutoPlay: false
          }));
          window.__meetingSockets = [];
          const NativeWebSocket = window.WebSocket;
          class TestSocket {
            static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
            readyState = 0;
            constructor(url, protocols) {
              if (!url.includes('/__ui_ws')) return new NativeWebSocket(url, protocols);
              window.__meetingSockets.push(this);
              setTimeout(() => { this.readyState = 1; this.onopen?.({}); }, 0);
            }
            send() {}
            close() { this.readyState = 3; this.onclose?.({code: 1000}); }
            emit(data) { this.onmessage?.({data: JSON.stringify(data)}); }
          }
          window.WebSocket = TestSocket;
        """)
        page = await context.new_page()
        errors, calls = [], []
        started = {count: asyncio.Event() for count in (60, 80, 100, 101, 121)}
        release = {count: asyncio.Event() for count in (60, 121)}
        completed = {count: asyncio.Event() for count in (60, 80, 100, 101, 121)}
        stale_load_started, release_stale_load = asyncio.Event(), asyncio.Event()
        delay_load = False
        page.on("pageerror", lambda error: errors.append(str(error)))
        messages = [{
            "id": f"saved-{i}", "speaker": f"speaker{i % 2 + 1}",
            "originalText": f"제품 출시 논의 {i}", "translatedText": "",
            "timestamp": f"2026-10-28T00:{i:02d}:00Z",
        } for i in range(1, 20)]
        meeting = {
            "meetingId": "meeting-1", "title": TITLE,
            "createdAt": "2026-10-28T00:00:00Z", "messageCount": 19,
            "messages": messages, "summary": "## 기존 상세 요약\n기존 내용을 유지합니다.",
        }

        async def route_api(route):
            request = route.request
            path = urlparse(request.url).path.removeprefix("/__ui_api")
            if request.method == "GET" and path == "/meetings":
                result = [{k: v for k, v in meeting.items() if k != "messages"}]
            elif request.method == "GET" and path == "/meetings/meeting-1":
                result = json.loads(json.dumps(meeting))
                if delay_load:
                    stale_load_started.set()
                    await release_stale_load.wait()
            elif request.method == "POST" and path == "/meetings":
                result = {"meetingId": "empty", "title": "Meeting new", "createdAt": "2026-10-28T01:00:00Z", "messages": [], "messageCount": 0}
            elif request.method == "POST" and path == "/meetings/meeting-1/summarize":
                body = request.post_data_json
                calls.append(body)
                assert body["mode"] == "brief", "The long auto-summary must remain disabled"
                count = len(body["messages"])
                # Model completion can lag behind persistence of the live utterances.
                epoch = datetime(2026, 10, 28, tzinfo=timezone.utc)
                meeting["messages"] = [{
                    "id": message["id"], "speaker": f"speaker{i % 2 + 1}",
                    "originalText": message["original"], "translatedText": "",
                    "timestamp": (epoch + timedelta(minutes=i)).isoformat(),
                } for i, message in enumerate(body["messages"])]
                meeting["messageCount"] = count
                if count in started:
                    started[count].set()
                if count in release:
                    await release[count].wait()
                if count == 100:
                    await route.fulfill(status=503, body="Temporarily unavailable")
                    completed[count].set()
                    return
                result = {
                    "meetingId": "meeting-1", "brief": BRIEF if count <= 40 else f"{BRIEF} · 검토 {count}",
                    "briefMessageCount": count,
                    "briefLastMessageId": body["messages"][-1]["id"],
                    "briefUpdatedAt": (epoch + timedelta(seconds=count)).isoformat(),
                }
                meeting.update(result)
            elif request.method == "DELETE":
                await route.fulfill(status=204)
                return
            else:
                raise AssertionError(f"Unexpected API: {request.method} {path}")
            await route.fulfill(status=200, content_type="application/json", body=json.dumps(result))
            if request.method == "POST" and path.endswith("/summarize") and count in completed:
                completed[count].set()

        async def add_messages(count):
            await page.evaluate("""count => {
              const socket = window.__meetingSockets.findLast(s => s.readyState === 1);
              for (let i = 0; i < count; i++) socket.emit({
                type: 'subtitle', speaker: 'speaker1',
                originalText: '결제 오류를 해결한 뒤 다음 주 출시합니다.',
                translatedText: '', timestamp: new Date().toISOString()
              });
            }""", count)

        await page.route("**/__ui_api/**", route_api)
        try:
            await page.goto(BASE + "/?meeting=meeting-1", wait_until="domcontentloaded")
            await expect(page.get_by_role("button", name=f"{TITLE} 제목 편집", exact=True)).to_be_visible()
            await expect(page.locator("main").get_by_label("대화 19개", exact=True)).to_be_visible()
            assert not calls
            await add_messages(1)
            await expect(page.get_by_label("회의 한 줄 요약", exact=True)).to_contain_text(BRIEF, timeout=3000)
            assert len(calls) == 1 and len(calls[0]["messages"]) == 20
            await expect(page.get_by_role("button", name=f"{TITLE} 제목 편집", exact=True)).to_be_visible()
            await add_messages(19)
            await expect(page.locator("main").get_by_label("대화 39개", exact=True)).to_be_visible()
            assert len(calls) == 1
            await add_messages(1)
            await expect(page.locator("main").get_by_label("대화 40개", exact=True)).to_be_visible()
            await expect(page.get_by_label("회의 한 줄 요약", exact=True)).to_have_attribute("aria-busy", "false")
            assert len(calls) == 2 and len(calls[1]["messages"]) == 40
            await page.set_viewport_size({"width": 390, "height": 844})
            await expect(page.get_by_label("회의 한 줄 요약", exact=True)).to_contain_text(BRIEF)
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            await page.screenshot(path="/tmp/transmeet-meeting-brief-mobile.png", full_page=True)
            await page.set_viewport_size({"width": 1280, "height": 900})
            await page.screenshot(path="/tmp/transmeet-meeting-brief-desktop.png", full_page=True)

            # New messages during a slow request must trigger a catch-up afterward.
            await add_messages(20)
            await asyncio.wait_for(started[60].wait(), timeout=5)
            await expect(page.get_by_label("회의 한 줄 요약")).to_contain_text(BRIEF)
            await add_messages(20)
            await expect(page.locator("main").get_by_label("대화 80개", exact=True)).to_be_visible()
            assert len(calls) == 3, "Do not send a duplicate while a brief is pending"
            release[60].set()
            await asyncio.wait_for(completed[80].wait(), timeout=5)
            await expect(page.get_by_label("회의 한 줄 요약")).to_contain_text("검토 80")

            # A failure retains the prior text; the next new message permits retry.
            await add_messages(20)
            await asyncio.wait_for(completed[100].wait(), timeout=5)
            await expect(page.get_by_label("회의 한 줄 요약")).to_have_attribute("aria-busy", "false")
            await expect(page.get_by_label("회의 한 줄 요약")).to_contain_text("검토 80")
            assert len(calls) == 5
            await add_messages(1)
            await asyncio.wait_for(completed[101].wait(), timeout=5)
            await expect(page.get_by_label("회의 한 줄 요약")).to_contain_text("검토 101")

            # An older GET response must not erase a brief that completed meanwhile.
            await add_messages(20)
            await asyncio.wait_for(started[121].wait(), timeout=5)
            delay_load = True
            await page.get_by_role("navigation", name="회의 기록").get_by_text(TITLE, exact=True).click()
            await asyncio.wait_for(stale_load_started.wait(), timeout=5)
            release[121].set()
            await asyncio.wait_for(completed[121].wait(), timeout=5)
            await expect(page.get_by_label("회의 한 줄 요약")).to_contain_text("검토 121")
            release_stale_load.set()
            await expect(page.locator("#meeting-voice-panel")).to_have_attribute("aria-busy", "false")
            await expect(page.get_by_label("회의 한 줄 요약")).to_contain_text("검토 121")
            delay_load = False

            # Refresh restores both the text and its checkpoint, with no extra request.
            await page.goto(BASE + "/?meeting=meeting-1", wait_until="domcontentloaded")
            await expect(page.locator("#meeting-voice-panel")).to_have_attribute("aria-busy", "false")
            await expect(page.get_by_label("회의 한 줄 요약")).to_contain_text("검토 121")
            await expect(page.get_by_role("button", name=f"{TITLE} 제목 편집", exact=True)).to_be_visible()
            assert len(calls) == 7
            assert not errors, errors
            print(json.dumps({"passed": True, "requestMessageCounts": [len(c["messages"]) for c in calls], "runtimeErrors": errors}))
        except Exception:
            print(json.dumps({"runtimeErrors": errors, "body": (await page.locator("body").inner_text())[:1800]}, ensure_ascii=False))
            raise
        finally:
            for gate in release.values():
                gate.set()
            release_stale_load.set()
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
