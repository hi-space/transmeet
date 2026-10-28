"""Verify recording controls with real browser audio and intercepted meeting APIs.

Start a dev server with Cognito disabled, NEXT_PUBLIC_API_ENDPOINT=<base>/__ui_api,
and NEXT_PUBLIC_WS_ENDPOINT=ws://<host>/__ui_ws. Then run this script with
TRANSMEET_TEST_URL=<base>. No meeting data or email is sent to a real backend.

For a deployed build, also set TRANSMEET_TEST_API_ENDPOINT,
TRANSMEET_TEST_CLIENT_ID and TRANSMEET_TEST_USER_POOL_ID to its public settings.
"""
import asyncio
import base64
import json
import os
from pathlib import Path
import time
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

BASE = os.environ.get("TRANSMEET_TEST_URL", "http://127.0.0.1:3187").rstrip("/")
API = os.environ.get("TRANSMEET_TEST_API_ENDPOINT", BASE + "/__ui_api").rstrip("/")
CLIENT_ID = os.environ.get("TRANSMEET_TEST_CLIENT_ID", "")
USER_POOL_ID = os.environ.get("TRANSMEET_TEST_USER_POOL_ID", "")
ARTIFACTS = Path(os.environ.get("TRANSMEET_TEST_ARTIFACTS", "/tmp/transmeet-controls"))

SOCKET = """
window.__recordingRequests = [];
window.__recordingSockets = [];
window.__socketOnline = true;
window.__capturedStreams = [];
const getUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia = async (...args) => {
  const stream = await getUserMedia(...args);
  window.__capturedStreams.push(stream);
  return stream;
};
const NativeWebSocket = window.WebSocket;
class RecordingSocket {
  static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
  readyState = 0;
  constructor(url, protocols) {
    const path = new URL(url).pathname;
    if (path !== '/__ui_ws' && path !== '/ws') return new NativeWebSocket(url, protocols);
    window.__recordingSockets.push(this);
    if (window.__socketOnline)
      setTimeout(() => { this.readyState = 1; this.onopen?.({}); }, 0);
  }
  send(raw) { window.__recordingRequests.push(JSON.parse(raw)); }
  close() { this.readyState = 3; this.onclose?.({code: 1000}); }
  emit(message) { this.onmessage?.({data: JSON.stringify(message)}); }
}
window.WebSocket = RecordingSocket;
"""


def token(access=False):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return ".".join([
        encode({"alg": "none"}),
        encode({
            "sub": "controls-test", "cognito:username": "controls-test",
            "email": "controls@example.com", "email_verified": True,
            "iat": int(time.time()), "exp": int(time.time()) + 3600,
            "token_use": "access" if access else "id",
        }),
        "test-signature",
    ])


async def scenario(browser, name, *, width, paused_end=False, disconnected=False, dark=False, narrow=False):
    context = await browser.new_context(
        viewport={"width": width, "height": 900}, service_workers="block",
        permissions=["microphone"], color_scheme="dark" if dark else "light",
    )
    await context.add_init_script("delete Navigator.prototype.serviceWorker")
    await context.add_init_script(SOCKET)
    settings = {"ttsAutoPlay": False, "audioSource": "mic", "sttProvider": "transcribe",
                "autoSummarizeMessageCount": 0, "emailOnEnd": True,
                "theme": "dark" if dark else "light"}
    await context.add_init_script(
        "localStorage.setItem('transmeet-settings', JSON.stringify("
        + json.dumps(settings) + "))"
    )
    if narrow:
        await context.add_init_script("""
          localStorage.setItem('transmeet-sidebar-width', '420');
          localStorage.setItem('transmeet-summary-width', '960');
        """)
    if CLIENT_ID:
        config = {"client": CLIENT_ID, "id": token(), "access": token(True)}
        await context.add_init_script("""(config => {
          const prefix = `CognitoIdentityServiceProvider.${config.client}`;
          localStorage.setItem(`${prefix}.LastAuthUser`, 'controls-test');
          for (const [key, value] of Object.entries({
            idToken: config.id, accessToken: config.access,
            refreshToken: 'test-refresh', clockDrift: '0'
          })) localStorage.setItem(`${prefix}.controls-test.${key}`, value);
        })""" + f"({json.dumps(config)})")

    page = await context.new_page()
    page.set_default_timeout(10000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    records = {
        f"meeting-{i}": {
            "meetingId": f"meeting-{i}", "title": f"프로젝트 진행 회의 {i}",
            "createdAt": f"2026-10-28T0{i}:00:00Z", "messageCount": 1,
            "messages": [{
                "id": f"message-{i}", "speaker": "speaker1",
                "originalText": "이번 주 진행 상황과 다음 일정을 확인합니다.",
                "translatedText": "Let's review this week's progress and next steps.",
                "timestamp": "2026-10-28T01:00:00Z",
            }],
        } for i in (1, 2)
    }
    if narrow:
        records["meeting-1"]["messages"] = [{
            "id": f"discussion-{i}", "speaker": f"speaker{1 + i % 2}",
            "originalText": f"안건 {i}. 다음 회의까지의 일정과 각 담당자의 업무를 확인합니다.",
            "translatedText": f"Discussion {i}. Confirm the delivery timeline and responsibilities.",
            "timestamp": f"2026-10-28T01:{i:02d}:00Z",
        } for i in range(1, 9)]
        records["meeting-1"]["messageCount"] = 8

    async def route_api(route):
        path = urlparse(route.request.url).path.removeprefix(urlparse(API).path)
        method = route.request.method
        headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*",
                   "Access-Control-Allow-Methods": "*"}
        if method == "OPTIONS":
            await route.fulfill(status=204, headers=headers)
            return
        if method == "GET" and path == "/meetings":
            result = list(records.values())
        elif method == "GET" and path.startswith("/meetings/"):
            result = records[path.split("/")[2]]
        elif method == "POST" and path == "/meetings":
            result = {"meetingId": "empty", "title": "새 회의",
                      "createdAt": "2026-10-28T03:00:00Z", "messages": [], "messageCount": 0}
        elif method == "GET" and path == "/me/email":
            result = {"email": "controls@example.com", "status": "verified"}
        elif method == "GET" and path == "/me/summary-templates":
            result = {"templates": []}
        elif method == "DELETE":
            await route.fulfill(status=204, headers=headers)
            return
        else:
            raise AssertionError(f"Unexpected API request: {method} {path}")
        await route.fulfill(status=200, headers=headers, content_type="application/json",
                            body=json.dumps(result))

    await page.route(API + "/**", route_api)
    if USER_POOL_ID:
        async def cognito(route):
            await route.fulfill(content_type="application/json", body=json.dumps({
                "Username": "controls-test", "UserAttributes": [
                    {"Name": "email", "Value": "controls@example.com"},
                    {"Name": "email_verified", "Value": "true"},
                ],
            }))
        await page.route("https://cognito-idp.*.amazonaws.com/**", cognito)

    async def requests(action):
        return await page.evaluate(
            "action => window.__recordingRequests.filter(r => r.action === action)", action)

    async def assert_audio_stopped():
        assert await page.evaluate(
            "window.__capturedStreams.every(s => s.getTracks().every(t => t.readyState === 'ended'))")

    try:
        await page.goto(BASE + "/?meeting=meeting-1", wait_until="domcontentloaded")
        await page.wait_for_function("window.__recordingSockets.some(s => s.readyState === 1)")
        start = page.get_by_role("button", name="녹음 시작", exact=True)
        await expect(start).to_be_enabled()
        await start.click()
        pause = page.get_by_role("button", name="녹음 일시정지", exact=True)
        end = page.get_by_role("button", name="회의 종료", exact=True)
        await expect(pause).to_be_visible()
        await expect(end).to_be_visible()
        await expect(end).to_have_count(1)
        await page.wait_for_function("window.__recordingRequests.some(r => r.action === 'sendAudio')")
        pause_icon = await pause.locator("svg path").get_attribute("d")
        end_icon = await end.locator("svg path").get_attribute("d")
        assert pause_icon != end_icon, "Pausing and ending must be visually distinct"
        for button in (pause, end):
            box = await button.bounding_box()
            assert box["width"] >= 44 and box["height"] >= 44, box
            assert box["x"] >= 0 and box["x"] + box["width"] <= width, box
            if narrow:
                panel = await page.get_by_role("tabpanel", name="대화", exact=True).bounding_box()
                assert box["x"] >= panel["x"], {"button": box, "panel": panel}
                assert box["x"] + box["width"] <= panel["x"] + panel["width"], {
                    "button": box, "panel": panel,
                }
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(ARTIFACTS / f"{name}-recording.png"), full_page=True)

        if disconnected:
            await page.evaluate("""() => {
              window.__socketOnline = false;
              window.__recordingSockets.filter(s => s.readyState === 1).forEach(s => s.close());
            }""")
            await expect(end).to_be_disabled()
            await expect(pause).to_be_enabled()
            await pause.click()
            await assert_audio_stopped()
            assert not await requests("endMeeting")
        else:
            await pause.click()
            resume = page.get_by_role("button", name="녹음 재개", exact=True)
            await expect(resume).to_be_visible()
            await expect(end).to_be_visible()
            await assert_audio_stopped()
            assert len(await requests("stopRecording")) == 1
            assert not await requests("endMeeting"), "Pausing must not generate pages or send email"
            if narrow:
                panel = page.get_by_role("tabpanel", name="대화", exact=True)
                await panel.locator(".overflow-y-auto").evaluate(
                    "element => { element.scrollTop = element.scrollHeight }")
                last_translation = panel.get_by_text(
                    "Discussion 8. Confirm the delivery timeline and responsibilities.", exact=True)
                text_box = await last_translation.bounding_box()
                controls_box = await page.get_by_role("group", name="녹음 조작", exact=True).bounding_box()
                assert text_box["y"] + text_box["height"] <= controls_box["y"], {
                    "lastTranslation": text_box, "controls": controls_box,
                }
            await page.screenshot(path=str(ARTIFACTS / f"{name}-paused.png"), full_page=True)

            if width >= 1024:
                nav = page.get_by_role("navigation", name="회의 기록")
                await nav.get_by_text(records["meeting-2"]["title"], exact=True).click()
                await expect(start).to_be_visible()
                await nav.get_by_text(records["meeting-1"]["title"], exact=True).click()
                await expect(resume).to_be_visible()

            if not paused_end:
                await resume.click()
                await expect(pause).to_be_visible()
                assert len(await requests("startRecording")) == 2
                page.once("dialog", lambda dialog: dialog.dismiss())
                await end.click()
                assert not await requests("endMeeting")
                await expect(pause).to_be_enabled()
                page.once("dialog", lambda dialog: dialog.accept())
            await end.click()
            await expect(page.get_by_role("button", name="회의 종료 중", exact=True)).to_be_disabled()
            await assert_audio_stopped()
            sent = await requests("endMeeting")
            assert len(sent) == 1 and sent[0]["meetingId"] == "meeting-1", sent
            assert sent[0]["emailOnEnd"] is True, sent
            assert len(await requests("stopRecording")) == 1
            await page.evaluate("""() => {
              window.__recordingSockets.filter(s => s.readyState === 1).forEach(s =>
                s.emit({type: 'meeting_end', meetingId: 'meeting-1', phase: 'done', status: 'skipped'}));
            }""")
            await expect(start).to_be_enabled()
            await expect(resume).to_have_count(0)
        assert not errors, errors
        print(json.dumps({"scenario": name, "passed": True, "runtimeErrors": errors}), flush=True)
    finally:
        await context.close()


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=[
            "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
        ])
        try:
            await scenario(browser, "desktop", width=1280)
            await scenario(browser, "narrow-desktop", width=1024, narrow=True)
            await scenario(browser, "mobile", width=375, paused_end=True)
            await scenario(browser, "compact-dark", width=320, dark=True)
            await scenario(browser, "disconnected", width=375, disconnected=True)
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
