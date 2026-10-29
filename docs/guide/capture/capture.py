"""TransMeet 사용 가이드 스크린샷 캡처.

실제 백엔드 대신 Playwright 로 REST·WebSocket·Cognito 를 가로채 샘플 데이터를 돌려준다.
개발 서버를 가짜 엔드포인트로 띄운 뒤 실행한다:

  NEXT_PUBLIC_API_ENDPOINT=https://api.guide.test NEXT_PUBLIC_WS_ENDPOINT=wss://ws.guide.test \
  NEXT_PUBLIC_COGNITO_USER_POOL_ID=us-east-1_GUIDE0000 NEXT_PUBLIC_COGNITO_CLIENT_ID=guideclient000 \
  NEXT_PUBLIC_COGNITO_REGION=us-east-1 npx next dev -p 3311
  python3 docs/guide/capture/capture.py            # 전체
  python3 docs/guide/capture/capture.py main       # 이름에 main 이 들어간 것만
"""

import asyncio
import base64
import json
import re
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright, Page, BrowserContext

sys.path.insert(0, str(Path(__file__).parent))
import mock_data as D  # noqa: E402

APP = "http://localhost:3311"
API = "https://api.guide.test"
OUT = Path(__file__).resolve().parent.parent / "images"
MOCK_PAGE = (Path(__file__).parent / "mock-page.html").read_text()
CLIENT_ID = "guideclient000"
USER = "guide-user"
EMAIL = "minji.kim@example.com"
CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
}


def b64url(obj) -> str:
    raw = json.dumps(obj).encode() if not isinstance(obj, bytes) else obj
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def fake_jwt(use: str) -> str:
    now = int(time.time())
    payload = {"sub": "guide-sub", "email": EMAIL, "email_verified": True, "token_use": use,
               "cognito:username": USER, "iat": now, "exp": now + 30 * 86400, "auth_time": now}
    return f"{b64url({'alg': 'none', 'kid': 'guide'})}.{b64url(payload)}.c2ln"


def init_storage_script(extra: dict) -> str:
    prefix = f"CognitoIdentityServiceProvider.{CLIENT_ID}"
    items = {
        f"{prefix}.LastAuthUser": USER,
        f"{prefix}.{USER}.idToken": fake_jwt("id"),
        f"{prefix}.{USER}.accessToken": fake_jwt("access"),
        f"{prefix}.{USER}.refreshToken": "guide-refresh",
        f"{prefix}.{USER}.clockDrift": "0",
        **extra,
    }
    return (
        "if (location.origin === %s && !sessionStorage.getItem('guide-init')) {"
        "  const items = %s;"
        "  for (const [k, v] of Object.entries(items)) localStorage.setItem(k, v);"
        "  sessionStorage.setItem('guide-init', '1');"
        "}" % (json.dumps(APP), json.dumps(items))
    )


class Mock:
    """요청별로 바꿀 수 있는 가짜 서버 상태."""

    def __init__(self, *, summary=True, page=True, logged_in=True, settings=None, theme=None,
                 email_verified=True):
        self.summary = summary
        self.email_verified = email_verified
        self.page = page
        self.logged_in = logged_in
        self.settings = settings
        self.theme = theme
        self.ws = None  # 마지막으로 연결된 WebSocketRoute
        self.stt_status = None  # sttStatus 요청에 돌려줄 엔진 상태

    # ─── REST ────────────────────────────────────────────────────────────────
    async def api(self, route):
        req = route.request
        if req.method == "OPTIONS":
            return await route.fulfill(status=204, headers=CORS)
        path = req.url.replace(API, "").split("?")[0]
        body = None
        if path == "/meetings" and req.method == "GET":
            body = D.meeting_list()
        elif path == "/meetings" and req.method == "POST":
            body = {"meetingId": "mtg-new", "title": "새 회의", "createdAt": D.iso(D.NOW), "messages": []}
        elif m := re.fullmatch(r"/meetings/([^/]+)", path):
            if req.method == "DELETE":
                return await route.fulfill(status=204, headers=CORS)
            mid = m.group(1)
            if mid == D.MAIN_ID:
                body = D.main_meeting(self.summary, self.page)
            else:
                base = next(x for x in D.meeting_list() if x["meetingId"] == mid)
                body = {**base, "messages": D.main_messages()[:4]}
        elif path.endswith("/summarize"):
            body = {"meetingId": D.MAIN_ID, "summary": D.SUMMARY, "summaryGeneratedStyle": "detailed"}
        elif path.endswith("/title"):
            body = {"meetingId": D.MAIN_ID, "title": D.meeting_list()[0]["title"]}
        elif path.endswith("/summary-style"):
            data = json.loads(req.post_data or "{}")
            body = {"meetingId": D.MAIN_ID, "summaryStyle": data.get("summaryStyle", "detailed")}
        elif path == "/me/summary-templates":
            body = {"templates": D.TEMPLATES}
        elif path.startswith("/me/email"):
            body = {"email": EMAIL, "status": "verified"}
        elif path == "/tts":
            body = {"audioData": "", "translatedText": "", "format": "mp3"}
        else:
            body = {}
        await route.fulfill(status=200, headers={**CORS, "Content-Type": "application/json"},
                            body=json.dumps(body, ensure_ascii=False))

    async def cognito(self, route):
        target = route.request.headers.get("x-amz-target", "")
        if target.endswith("ForgotPassword"):
            body = {"CodeDeliveryDetails": {"Destination": "m***@e***", "DeliveryMedium": "EMAIL",
                                            "AttributeName": "email"}}
        elif target.endswith("GetUser"):
            body = {"Username": USER, "UserAttributes": [
                {"Name": "email", "Value": EMAIL},
                {"Name": "email_verified", "Value": "true" if self.email_verified else "false"}]}
        else:
            body = {}
        await route.fulfill(status=200, headers={**CORS, "Content-Type": "application/x-amz-json-1.1"},
                            body=json.dumps(body))

    # ─── WebSocket ───────────────────────────────────────────────────────────
    def on_ws(self, ws):
        self.ws = ws

        def handle(message):
            try:
                msg = json.loads(message)
            except Exception:
                return
            action = msg.get("action")
            if action in ("sttStatus", "prepareStt") and self.stt_status:
                st = dict(self.stt_status, provider=msg.get("sttProvider", self.stt_status["provider"]))
                ws.send(json.dumps({"type": "stt_status", **st}))
            if action == "ttsRequest":
                ws.send(json.dumps({"type": "tts_stream", "messageId": msg["messageId"], "phase": "done",
                                    "translatedText": D.MEMO_EN, "timestamp": D.iso(D.NOW)}))
            if action == "qaRequest":
                ws.send(json.dumps({"type": "qa_stream", "messageId": msg["messageId"], "phase": "done",
                                    "answer": D.QA_ANSWER}, ensure_ascii=False))
            if action == "ping":
                ws.send(json.dumps({"type": "pong", "timestamp": D.iso(D.NOW)}))
            elif action == "translateMessage" and msg.get("messageId") == "__quick__":
                src = msg.get("originalText", "")
                ko = bool(re.search("[가-힣]", src))
                out = ("Let's lock in the launch date at the go/no-go review." if ko
                       else "출시일은 Go/No-go 리뷰에서 확정합시다.")
                ws.send(json.dumps({"type": "subtitle_stream", "messageId": "__quick__", "phase": "done",
                                    "originalText": src, "translatedText": out,
                                    "detectedLanguage": "ko" if ko else "en",
                                    "targetLanguage": "en" if ko else "ko", "timestamp": D.iso(D.NOW)}))

        ws.on_message(handle)

    def push(self, payload: dict):
        if self.ws:
            self.ws.send(json.dumps(payload, ensure_ascii=False))


async def new_page(browser, mock: Mock, *, width=1280, height=800, mobile=False, dark=False) -> Page:
    extra = {}
    if mock.settings:
        extra["transmeet-settings"] = json.dumps(mock.settings)
    extra["transmeet-theme"] = json.dumps(mock.theme or {"mode": "dark" if dark else "light"})
    extra["transmeet-quick-translate-history"] = json.dumps(D.QUICK_HISTORY, ensure_ascii=False)
    ctx: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=2,
        is_mobile=mobile,
        has_touch=mobile,
        locale="ko-KR",
        timezone_id="Asia/Seoul",
        color_scheme="dark" if dark else "light",
        permissions=["microphone"],
    )
    await ctx.clock.install(time=D.NOW.timestamp())
    await ctx.clock.resume()
    if mock.logged_in:
        await ctx.add_init_script(init_storage_script(extra))
    await ctx.route(f"{API}/**", mock.api)
    await ctx.route("https://cognito-idp.us-east-1.amazonaws.com/**", mock.cognito)
    await ctx.route(re.compile(r".*/p/pg-launch.*"),
                    lambda r: r.fulfill(status=200, body=MOCK_PAGE,
                                        headers={**CORS, "Content-Type": "text/html; charset=utf-8"}))
    await ctx.route_web_socket(re.compile(r"wss://ws\.guide\.test.*"), mock.on_ws)
    page = await ctx.new_page()
    return page


async def open_app(page: Page):
    await page.goto(APP, wait_until="networkidle")
    await page.wait_for_timeout(1200)


async def shot(page: Page, name: str, *, clip_selector: str | None = None, full=False, pad=0, max_h=None, from_bottom=False):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    await page.wait_for_timeout(400)
    if clip_selector:
        box = await page.locator(clip_selector).first.bounding_box()
        clip = {"x": max(0, box["x"] - pad), "y": max(0, box["y"] - pad),
                "width": box["width"] + pad * 2, "height": box["height"] + pad * 2}
        if max_h and clip["height"] > max_h:
            if from_bottom:
                clip["y"] += clip["height"] - max_h
            clip["height"] = max_h
        await page.screenshot(path=path, clip=clip)
    else:
        await page.screenshot(path=path, full_page=full)
    print("saved", path.name)


# ─── 강조 표시 ──────────────────────────────────────────────────────────────

ANNOTATE_JS = """
(marks) => {
  document.querySelectorAll('.guide-mark').forEach(e => e.remove());
  const accent = '#e8590c';
  marks.forEach(([sel, label, pos], i) => {
    const el = typeof sel === 'string' ? document.querySelector(sel) : null;
    if (!el) { console.warn('guide: no element', sel); return; }
    const r = el.getBoundingClientRect();
    const box = document.createElement('div');
    box.className = 'guide-mark';
    Object.assign(box.style, {position:'fixed', left:(r.left-4)+'px', top:(r.top-4)+'px',
      width:(r.width+8)+'px', height:(r.height+8)+'px', border:'2.5px solid '+accent,
      borderRadius:'10px', zIndex:99998, pointerEvents:'none', boxSizing:'border-box'});
    document.body.appendChild(box);
    const dot = document.createElement('div');
    dot.className = 'guide-mark';
    dot.textContent = label;
    const p = pos || 'tl';
    const cx = p.includes('r') ? r.right + 4 : r.left - 4;
    const cy = p.includes('b') ? r.bottom + 4 : r.top - 4;
    Object.assign(dot.style, {position:'fixed', left:(cx-12)+'px', top:(cy-12)+'px', width:'24px',
      height:'24px', borderRadius:'50%', background:accent, color:'#fff', font:'700 13px/24px system-ui',
      textAlign:'center', zIndex:99999, pointerEvents:'none', boxShadow:'0 1px 4px rgba(0,0,0,.3)'});
    document.body.appendChild(dot);
  });
}
"""


async def annotate(page: Page, marks):
    """marks: [(css selector, '1', 'tl'|'tr'|'bl'|'br'), ...]"""
    await page.evaluate(ANNOTATE_JS, [list(m) + [None] * (3 - len(m)) for m in marks])


async def clear_marks(page: Page):
    await page.evaluate("document.querySelectorAll('.guide-mark').forEach(e => e.remove())")


async def open_main_meeting(page: Page):
    await page.locator('aside[aria-label="회의 목록"] button', has_text="시애틀 팀과").first.click()
    await page.wait_for_timeout(1500)


# ─── 시나리오 ────────────────────────────────────────────────────────────────

AUTH_CARD = "div.max-w-sm"


async def s_login(browser):
    page = await new_page(browser, Mock(logged_in=False), height=860)
    await page.goto(APP, wait_until="networkidle")
    await page.wait_for_timeout(800)
    await page.fill('input[type="email"]', EMAIL)
    await page.fill('input[type="password"]', "password1234")
    await annotate(page, [
        ('input[type="email"]', "1", "tr"), ('input[type="password"]', "2", "tr"),
        ('section.ui-surface button[type="submit"]', "3", "tr"),
        ('section.ui-surface button:not([type="submit"])', "4", "tr"),
    ])
    await shot(page, "01-login", clip_selector=AUTH_CARD, pad=24)
    await clear_marks(page)
    await page.locator("button", has_text="비밀번호 찾기").click()
    await page.wait_for_timeout(400)
    await page.fill('input[type="email"]', EMAIL)
    await shot(page, "01b-forgot", clip_selector=AUTH_CARD, pad=24)
    await page.locator('button[type="submit"]').click()
    await page.wait_for_timeout(800)
    await shot(page, "01c-reset-code", clip_selector=AUTH_CARD, pad=24)


async def s_account(browser):
    for name, verified in [("25-settings-account", True), ("25b-settings-account-unverified", False)]:
        page = await new_page(browser, Mock(email_verified=verified), height=900)
        await open_app(page)
        await page.click('header button[aria-label="설정"]')
        await page.wait_for_timeout(500)
        await page.locator('dialog[open] [role="tab"]', has_text="계정").click()
        await page.wait_for_timeout(1200)
        await shot(page, name, clip_selector="dialog[open]")


async def s_memo_qa(browser):
    page = await new_page(browser, Mock(settings={"ttsAutoPlay": False}))
    await open_app(page)
    await open_main_meeting(page)
    tools = 'aside[aria-label="회의 도구"]'
    await page.locator(f'{tools} [role="tab"]', has_text="내 메모").click()
    await page.wait_for_timeout(400)
    await page.fill('textarea[aria-label="내 메모 입력"]', D.MEMO_KO)
    await page.click('button[aria-label="메모 보내기"]')
    await page.wait_for_timeout(2000)
    await page.locator(f"{tools} .scrollbar-thin").first.evaluate("e => e.scrollTo(0, e.scrollHeight)")
    await page.wait_for_timeout(300)
    await shot(page, "09b-notes-qa", clip_selector=tools)


async def s_paused(browser):
    page = await new_page(browser, Mock())
    await open_app(page)
    await open_main_meeting(page)
    await page.click('[aria-label="녹음 조작"] button[aria-label="녹음 시작"]')
    await page.wait_for_timeout(1200)
    await page.click('[aria-label="녹음 조작"] button[aria-label="녹음 일시정지"]')
    await page.wait_for_timeout(800)
    await annotate(page, [
        ('[aria-label="녹음 조작"] button[aria-label="녹음 재개"]', "1", "tl"),
        ('[aria-label="녹음 조작"] button[aria-label="회의 종료"]', "2", "tr"),
    ])
    await shot(page, "13d-paused", clip_selector="#meeting-voice-panel", max_h=360, from_bottom=True)


async def s_confirms(browser):
    page = await new_page(browser, Mock())
    await open_app(page)
    await open_main_meeting(page)
    side = 'aside[aria-label="회의 목록"]'
    await page.locator(f'{side} button[title="회의 작업"]').nth(1).click()
    await page.locator(f'{side} button', has_text="회의 삭제").click()
    await page.wait_for_timeout(400)
    await shot(page, "07b-delete-confirm", clip_selector=side, max_h=520)
    await page.keyboard.press("Escape")
    tools = 'aside[aria-label="회의 도구"]'
    await page.locator(f'{tools} [role="tab"]', has_text="회의 페이지").click()
    await page.wait_for_timeout(1500)
    await page.locator(f'{tools} [aria-label="회의 페이지 작업"] button', has_text="다시 만들기").click()
    await page.wait_for_timeout(500)
    await shot(page, "11b-page-regen-confirm", clip_selector=tools)


async def s_interval(browser):
    page = await new_page(browser, Mock(settings={"partialTranslationMode": "realtime"}), height=900)
    await open_app(page)
    await page.click('header button[aria-label="설정"]')
    await page.wait_for_timeout(500)
    await page.locator('dialog[open] summary', has_text="고급 설정").click()
    await page.locator('#settings-content').evaluate("e => e.scrollTo(0, e.scrollHeight)")
    await page.wait_for_timeout(400)
    await shot(page, "18b-settings-interval", clip_selector="dialog[open]")


async def s_main(browser):
    page = await new_page(browser, Mock())
    await open_app(page)
    await shot(page, "03-new-meeting-empty")
    await open_main_meeting(page)
    await annotate(page, [
        ('aside[aria-label="회의 목록"]', "1", "tr"),
        ('header [aria-label="음성 언어"]', "2", "bl"),
        ('header button[aria-label="빠른 번역"]', "3", "bl"),
        ('header button[aria-label="설정"]', "4", "bl"),
        ('#meeting-voice-panel', "5"),
        ('aside[aria-label="회의 도구"]', "6", "tr"),
        ('[aria-label="녹음 조작"]', "7"),
    ])
    await shot(page, "02-main-overview")


async def s_meeting(browser):
    page = await new_page(browser, Mock())
    await open_app(page)
    await open_main_meeting(page)
    # 회의 제목·한 줄 요약
    await annotate(page, [
        ('main .meeting-heading button[title="회의 제목 편집"]', "1", "bl"),
        ('main .meeting-heading .meeting-metadata', "2", "tr"),
        ('main [role="status"][aria-label="회의 한 줄 요약"]', "3", "bl"),
    ])
    await shot(page, "05-meeting-heading", clip_selector="main .meeting-heading", pad=14)
    await clear_marks(page)
    # 말풍선 버튼
    bubble = page.locator('#meeting-voice-panel article').nth(1)
    await bubble.hover()
    await annotate(page, [
        ('#meeting-voice-panel article:nth-of-type(2) button[title="다시 번역"]', "1", "tr"),
        ('#meeting-voice-panel article:nth-of-type(2) button[title="원문 음성 재생"]', "2", "tr"),
        ('#meeting-voice-panel .ui-panel-header button[title="전체 대화 원문 복사"]', "3", "bl"),
        ('#meeting-voice-panel [aria-label="대화 글자 크기"]', "4", "br"),
    ])
    await shot(page, "06-voice-panel", clip_selector="#meeting-voice-panel", pad=16)
    await clear_marks(page)
    # 회의 목록 작업 메뉴
    await page.locator('aside[aria-label="회의 목록"] button[title="회의 작업"]').nth(1).click()
    await page.wait_for_timeout(400)
    await shot(page, "07-sidebar-menu", clip_selector='aside[aria-label="회의 목록"]', max_h=640)
    await page.keyboard.press("Escape")
    # 제목 편집
    await page.locator('main .meeting-heading button[title="회의 제목 편집"]').click()
    await page.wait_for_timeout(300)
    await shot(page, "08-title-edit", clip_selector="main .meeting-heading", pad=6)


async def s_tools(browser):
    page = await new_page(browser, Mock())
    await open_app(page)
    await open_main_meeting(page)
    tools = 'aside[aria-label="회의 도구"]'
    # 요약
    await annotate(page, [
        (f'{tools} select[aria-label="요약 방식"]', "1", "bl"),
        (f'{tools} button[aria-label="요약 새로 생성"]', "2", "br"),
    ])
    await shot(page, "10-summary", clip_selector=tools, pad=16)
    await clear_marks(page)
    # 내 메모
    await page.locator(f'{tools} [role="tab"]', has_text="내 메모").click()
    await page.wait_for_timeout(500)
    await page.fill('textarea[aria-label="내 메모 입력"]', "일정 초대 받으면 QA 담당자도 포함해 달라고 요청하기")
    await annotate(page, [
        ('textarea[aria-label="내 메모 입력"]', "1"),
        ('button[aria-label="메모 보내기"]', "2", "tr"),
    ])
    await shot(page, "09-notes", clip_selector=tools, pad=16)
    await clear_marks(page)
    # 회의 페이지
    await page.locator(f'{tools} [role="tab"]', has_text="회의 페이지").click()
    await page.wait_for_timeout(2500)
    await annotate(page, [
        (f'{tools} button[title="회의 페이지 공유"]', "1", "bl"),
        (f'{tools} a[title="새 탭에서 열기"]', "2", "bl"),
        (f'{tools} [aria-label="회의 페이지 작업"]', "3", "tl"),
    ])
    await shot(page, "11-page", clip_selector=tools, pad=16)
    await clear_marks(page)


async def s_page_empty(browser):
    page = await new_page(browser, Mock(page=False, summary=False, settings={"autoSummarizeMessageCount": 0}))
    await open_app(page)
    await open_main_meeting(page)
    tools = 'aside[aria-label="회의 도구"]'
    await shot(page, "10b-summary-empty", clip_selector=tools, pad=8)
    await page.locator(f'{tools} [role="tab"]', has_text="회의 페이지").click()
    await page.wait_for_timeout(800)
    await shot(page, "12-page-create", clip_selector=tools, pad=8)


async def s_recording(browser):
    mock = Mock()
    page = await new_page(browser, mock)
    await open_app(page)
    await open_main_meeting(page)
    await annotate(page, [('[aria-label="녹음 조작"] button[aria-label="녹음 시작"]', "1", "tl")])
    await shot(page, "13a-record-start", clip_selector="#meeting-voice-panel")
    await clear_marks(page)
    await page.click('[aria-label="녹음 조작"] button[aria-label="녹음 시작"]')
    await page.wait_for_timeout(1500)
    mock.push({"type": "subtitle_stream", "messageId": "live-1", "phase": "stt_partial", "speaker": "speaker1",
               "timestamp": D.iso(D.NOW), "originalText": "One more thing, could we also prepare a short demo video for the",
               "detectedLanguage": "en", "sttProvider": "transcribe"})
    await page.wait_for_timeout(800)
    await annotate(page, [
        ('[aria-label="녹음 조작"] button[aria-label="녹음 작업 더보기"]', "1", "tl"),
        ('[aria-label="녹음 조작"] button[aria-label="녹음 일시정지"]', "2", "tl"),
        ('[aria-label="녹음 조작"] button[aria-label="회의 종료"]', "3", "tr"),
    ])
    await shot(page, "13b-recording")
    await clear_marks(page)
    await page.click('[aria-label="녹음 조작"] button[aria-label="녹음 작업 더보기"]')
    await page.wait_for_timeout(400)
    await shot(page, "14-more-menu", clip_selector="#meeting-voice-panel")


async def s_end(browser):
    mock = Mock()
    page = await new_page(browser, mock)
    await open_app(page)
    await open_main_meeting(page)
    await page.wait_for_timeout(500)
    mock.push({"type": "meeting_end", "meetingId": D.MAIN_ID, "phase": "done", "status": "sent"})
    await page.wait_for_timeout(600)
    await shot(page, "15-meeting-ended", clip_selector="#meeting-voice-panel", max_h=420, from_bottom=True)


async def s_quick(browser):
    page = await new_page(browser, Mock())
    await open_app(page)
    await open_main_meeting(page)
    await page.click('header button[aria-label="빠른 번역"]')
    await page.wait_for_timeout(600)
    dlg = 'dialog[open]'
    await page.locator(f'{dlg} textarea').fill("출시일은 Go/No-go 리뷰에서 확정하죠.")
    await page.locator(f'{dlg} button', has_text="번역하기").click()
    await page.wait_for_timeout(1200)
    await annotate(page, [
        (f'{dlg} [aria-label="빠른 번역 방향"]', "1", "tl"),
        (f'{dlg} textarea', "2", "tl"),
    ])
    await clear_marks(page)
    box = page.locator(dlg).first
    await shot(page, "16b-quick-translate-popup", clip_selector=dlg)


async def s_settings(browser):
    page = await new_page(browser, Mock(settings={"emailOnEnd": True}))
    await open_app(page)
    await open_main_meeting(page)
    await page.click('header button[aria-label="설정"]')
    await page.wait_for_timeout(800)
    dlg = 'dialog[open]'
    await shot(page, "17-settings-general", clip_selector=dlg)
    await page.locator(f'{dlg} summary', has_text="고급 설정").click()
    await page.wait_for_timeout(400)
    await page.locator(f'{dlg} details').scroll_into_view_if_needed()
    await page.locator('#settings-content').evaluate("e => e.scrollTo(0, e.scrollHeight)")
    await page.wait_for_timeout(300)
    await shot(page, "18-settings-advanced", clip_selector=dlg)
    await page.locator(f'{dlg} [role="tab"]', has_text="테마").click()
    await page.wait_for_timeout(500)
    await shot(page, "19-settings-theme", clip_selector=dlg)
    await page.locator('#settings-content').evaluate("e => e.scrollTo(0, e.scrollHeight)")
    await page.wait_for_timeout(300)
    await page.locator(f'{dlg} [role="tab"]', has_text="요약 양식").click()
    await page.wait_for_timeout(500)
    await shot(page, "20-settings-templates", clip_selector=dlg)
    await page.locator(f'{dlg} button', has_text="새 양식").click()
    await page.wait_for_timeout(400)
    await page.fill('input[aria-label="양식 이름"]', "고객 미팅 보고")
    await page.fill('textarea[aria-label="양식 내용"]',
                    "1) 고객 요구 2) 우리가 제안한 것 3) 고객 반응 4) 다음 액션(담당·기한) 순서로 정리해 주세요.")
    await shot(page, "21-settings-template-new", clip_selector=dlg)


async def s_dark(browser):
    page = await new_page(browser, Mock(), dark=True)
    await open_app(page)
    await open_main_meeting(page)
    await shot(page, "22-dark-mode")


async def s_mobile(browser):
    for name, tab in [("23a-mobile-voice", None), ("23b-mobile-summary", "요약")]:
        page = await new_page(browser, Mock(), width=390, height=844, mobile=True)
        await open_app(page)
        await page.click('header button[aria-label="회의 목록 열기"]')
        await page.wait_for_timeout(600)
        if name == "23a-mobile-voice":
            await shot(page, "23c-mobile-drawer")
        await page.locator('dialog[open] button', has_text="시애틀 팀과").first.click()
        await page.wait_for_timeout(1500)
        if tab:
            await page.locator('[role="tab"]', has_text=tab).first.click()
            await page.wait_for_timeout(600)
        await shot(page, name)
        if not tab:
            await page.click('header button[title="언어 및 화면 설정"]')
            await page.wait_for_timeout(500)
            await shot(page, "23d-mobile-quick-settings")


async def s_stt(browser):
    mock = Mock(settings={"sttProvider": "qwen3-asr"})
    mock.stt_status = {"provider": "qwen3-asr", "status": "sleeping", "managed": True, "streaming": True,
                       "currentCopies": 0, "desiredCopies": 0}
    page = await new_page(browser, mock)
    await open_app(page)
    await open_main_meeting(page)
    mock.push({"type": "stt_status", **mock.stt_status})
    await page.wait_for_timeout(800)
    await shot(page, "24-stt-sleeping", clip_selector="#meeting-voice-panel", max_h=420, from_bottom=True)
    mock.push({"type": "stt_status", **dict(mock.stt_status, status="starting", desiredCopies=1)})
    await page.wait_for_timeout(600)
    await shot(page, "24b-stt-starting", clip_selector="#meeting-voice-panel", max_h=420, from_bottom=True)


SCENARIOS = {name[2:]: fn for name, fn in list(globals().items()) if name.startswith("s_")}


async def main():
    only = sys.argv[1:] or None
    async with async_playwright() as p:
        import os
        env = {**os.environ, "FONTCONFIG_FILE": str(Path(__file__).parent / "fonts.conf")}
        browser = await p.chromium.launch(env=env, args=["--use-fake-ui-for-media-stream",
                                                         "--use-fake-device-for-media-stream",
                                                         "--font-render-hinting=none"])
        for name, fn in SCENARIOS.items():
            if only and not any(o in name for o in only):
                continue
            await fn(browser)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
