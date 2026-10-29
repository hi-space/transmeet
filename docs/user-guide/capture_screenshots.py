"""Capture the real TransMeet UI with invented examples and intercepted services.

Run from the repository root:
    python3 docs/user-guide/capture_screenshots.py

Requires the existing node_modules, infra/node_modules/esbuild, Python Playwright
and Pillow. A temporary preview server is created and removed automatically.
No production credentials, API, model invocation, email or publication is used.
"""

import asyncio
import argparse
import base64
import functools
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from PIL import Image
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "screenshots"
CLIENT = "guide-preview-client"
DAY = "2026-10-29"
TITLE = "글로벌 제품팀 · 출시 일정 회의"
SUMMARY = """## 한눈에 보기
**11월 6일 출시를 유지하고, 사용자 안내를 먼저 보강합니다.**

## 주요 논의
- 처음 사용하는 고객이 설정 화면에서 어려움을 겪고 있습니다.
- 한국어·영어 사용 가이드를 준비합니다.
- 출시 전에 5명의 사용자와 사용성을 확인합니다.

## 결정 사항
1. 첫 화면에 시작 순서를 안내합니다.
2. 자주 쓰는 설정을 가이드에 포함합니다.

## 다음 할 일
| 담당 | 할 일 | 기한 |
| --- | --- | --- |
| 제품팀 | 사용 가이드 초안 | 10월 30일 |
| 디자인팀 | 화면 캡처 검토 | 11월 2일 |

## 미결 사항
모바일 안내의 추가 범위를 다음 회의에서 정합니다.
"""

PAGE_HTML = """<!doctype html><html lang="ko"><meta charset="utf-8">
<style>*{box-sizing:border-box}body{margin:0;padding:30px 22px;font-family:system-ui,sans-serif;color:#263e39;background:#fbfaf6;font-size:13px;line-height:1.8}
small{color:#507c72;font-weight:700;letter-spacing:1px}h1{font-size:26px;line-height:1.4;margin:12px 0 22px}h2{font-size:16px;border-top:1px solid #d9e0d7;padding-top:18px;margin-top:24px}
.lead{background:#e7efdf;padding:18px;border-radius:8px;font-weight:600}.step{padding:10px 0;border-bottom:1px solid #e4e5dc}
.tag{font-size:11px;color:#607069;margin-top:20px}</style>
<small>MEETING NOTES · 2026.10.29</small>
<h1>가이드를 보강하고,<br>11월 6일 출시합니다.</h1>
<div class="lead">제품팀과 디자인팀이 첫 사용 경험을 함께 점검합니다.</div>
<h2>결정한 내용</h2><div class="step">01　첫 화면에 시작 순서 안내</div><div class="step">02　한국어·영어 가이드 준비</div>
<h2>다음 할 일</h2><p>제품팀 · 가이드 초안 · 10월 30일<br>디자인팀 · 화면 검토 · 11월 2일</p>
<h2>남은 질문</h2><p>모바일 안내의 범위는 다음 회의에서 정합니다.</p>
<p class="tag">사용 가이드용 예제 회의 페이지</p></html>"""


def records():
    utterances = [
        ("speaker1", "Let's confirm our launch plan for November 6.",
         "11월 6일 출시 계획을 확인하겠습니다."),
        ("speaker2", "New users need a clearer guide to the settings.",
         "처음 사용하는 분들을 위해 설정 가이드를 더 알기 쉽게 만들어야 합니다."),
        ("speaker1", "Let's prepare the Korean and English guides first.",
         "한국어와 영어 가이드부터 준비합시다."),
        ("speaker2", "The design team can review the screenshots by November 2.",
         "디자인팀이 11월 2일까지 스크린샷을 검토할 수 있습니다."),
    ]
    messages = [{
        "id": f"voice-{i}", "speaker": speaker, "originalText": original,
        "translatedText": translated, "detectedLanguage": "en", "targetLanguage": "ko",
        "timestamp": f"{DAY}T09:{i:02d}:00Z",
    } for i, (speaker, original, translated) in enumerate(utterances, 1)]
    messages.append({
        "id": "note-1", "speaker": "me",
        "originalText": "I'll share the first draft by tomorrow.",
        "translatedText": "내일까지 초안을 공유하겠습니다.",
        "timestamp": f"{DAY}T09:05:00Z",
    })
    return {
        "guide-meeting": {
            "meetingId": "guide-meeting", "title": TITLE,
            "createdAt": f"{DAY}T09:00:00Z", "messageCount": len(messages),
            "messages": messages, "summary": SUMMARY, "summaryStyle": "detailed",
            "summaryGeneratedStyle": "detailed",
            "brief": "11월 6일 출시 유지 · 사용 가이드 보강 · 다음 주 화면 검토",
            "briefMessageCount": len(messages), "briefLastMessageId": "note-1",
            "briefUpdatedAt": f"{DAY}T09:06:00Z", "hasAudio": True,
        },
        "guide-design": {
            "meetingId": "guide-design", "title": "디자인 리뷰 · 첫 사용 경험",
            "createdAt": "2026-10-28T14:00:00Z", "messageCount": 2,
            "messages": messages[:2], "brief": "시작 안내와 설정 설명 개선",
        },
        "guide-weekly": {
            "meetingId": "guide-weekly", "title": "주간 팀 미팅",
            "createdAt": "2026-10-27T10:00:00Z", "messageCount": 2,
            "messages": messages[:2],
        },
    }


def token(verified=True, access=False):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return ".".join([
        encode({"alg": "none"}),
        encode({"sub": "guide-user", "cognito:username": "guide-user",
                "email": "guide@example.com", "email_verified": verified,
                "iat": int(time.time()), "exp": int(time.time()) + 3600,
                "token_use": "access" if access else "id"}),
        "documentation-preview-only",
    ])


SOCKET = r"""
window.__guideSockets = [];
window.__guideRequests = [];
window.__guideStreaming = true;
class GuideSocket {
  static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
  readyState = 0;
  constructor(url) {
    this.meetingId = new URL(url).searchParams.get('meetingId');
    window.__guideSockets.push(this);
    setTimeout(() => { this.readyState = 1; this.onopen?.({}); }, 0);
  }
  send(raw) {
    const data = JSON.parse(raw);
    window.__guideRequests.push(data);
    if (data.action === 'ping') this.emit({type:'pong'});
    if (data.action === 'sttStatus' || data.action === 'prepareStt') {
      this.emit({type:'stt_status', provider:data.sttProvider || data.provider,
        status:'ready', streaming:window.__guideStreaming, managed:true,
        message:'엔진이 준비되었습니다. 녹음을 시작할 수 있습니다.'});
    }
    if (data.action === 'translateMessage') {
      this.emit({type:'subtitle_stream', phase:'done', messageId:data.messageId,
        originalText:data.originalText, translatedText:'Could you explain that one more time?',
        timestamp:'2026-10-29T09:06:00Z'});
    }
    if (data.action === 'ttsRequest') {
      this.emit({type:'tts_stream', phase:'done', messageId:data.messageId,
        translatedText:'What did we decide about the launch date?',
        timestamp:'2026-10-29T09:06:00Z'});
    }
    if (data.action === 'qaRequest') {
      this.emit({type:'qa_stream', phase:'done', messageId:data.messageId,
        answer:'대화에서는 **11월 6일 출시를 유지**하기로 했습니다.\n\n제품팀은 10월 30일까지 가이드 초안을 공유하고, 디자인팀은 11월 2일까지 화면을 검토합니다.'});
    }
    if (data.action === 'summarize') {
      this.emit({type:'summary_stream', phase:'done', meetingId:this.meetingId,
        summaryStyle:data.summaryStyle, summary:window.__guideSummary});
    }
  }
  emit(data) { setTimeout(() => this.onmessage?.({data:JSON.stringify(data)}), 30); }
  close(code = 1000) { this.readyState = 3; this.onclose?.({code}); }
}
window.WebSocket = GuideSocket;
"""


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def build_preview(directory):
    build = r"""
const fs = require('node:fs');
const path = require('node:path');
const cfg = JSON.parse(fs.readFileSync(0, 'utf8'));
require('./infra/node_modules/esbuild').buildSync({
  entryPoints: ['docs/user-guide/capture-entry.tsx'],
  bundle: true, outfile: path.join(cfg.directory, 'app.js'), jsx: 'automatic',
  format: 'esm', alias: {'@': path.join(process.cwd(), 'src')},
  define: {
    'process.env.NODE_ENV': '"production"',
    'process.env.NEXT_PUBLIC_API_ENDPOINT': '"/__guide_api"',
    'process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID': '"us-east-1_GUIDE"',
    'process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID': '"guide-preview-client"',
    'process.env.NEXT_PUBLIC_WS_ENDPOINT': '"ws://guide.invalid/ws"',
    global: 'globalThis',
  },
});
"""
    subprocess.run(["node", "-e", build], cwd=ROOT,
                   input=json.dumps({"directory": str(directory)}),
                   text=True, check=True, capture_output=True)
    subprocess.run(
        ["node", "node_modules/tailwindcss/lib/cli.js", "-i", "src/app/globals.css",
         "-o", str(directory / "styles.css")],
        cwd=ROOT, check=True, capture_output=True,
    )
    # Reuse cached Next font assets where available; avoid fetching web fonts.
    cached_css = "\n".join(p.read_text() for p in (ROOT / ".next/static/css").rglob("*.css"))
    faces = re.findall(r"@font-face\s*\{[^}]+\}", cached_css)
    fonts = "\n".join(faces)
    fonts = re.sub(r"(?:\.\./)+media/", "/_next/static/media/", fonts)
    variables = []
    for var, family in [
        ("geist-sans", "Inter"), ("geist-mono", "JetBrains_Mono"),
        ("noto-sans", "Noto_Sans_KR"), ("ibm-plex", "IBM_Plex_Sans_KR"),
    ]:
        match = re.search(r"font-family:\s*['\"]?(__" + family + r"_\w+)", fonts)
        if match:
            variables.append(f"--font-{var}:'{match.group(1)}'")
    fonts += ":root{" + ";".join(variables) + "}"
    (directory / "fonts.css").write_text(fonts)
    if (ROOT / ".next/static/media").exists():
        (directory / "_next/static").mkdir(parents=True)
        (directory / "_next/static/media").symlink_to(ROOT / ".next/static/media")
    (directory / "brand").symlink_to(ROOT / "public/brand")
    # The audio hook creates a module worker relative to the bundled entry.
    worker_dir = directory.parent / "workers"
    worker_dir.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "src/workers/audio-timer.worker.ts", worker_dir / "audio-timer.worker.ts")
    (directory / "index.html").write_text(
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<link rel="stylesheet" href="/fonts.css"><link rel="stylesheet" href="/styles.css">'
        '<title>TransMeet · 사용 가이드 촬영</title></head>'
        '<body><div id="root"></div><script type="module" src="/app.js"></script></body></html>'
    )


async def run_capture(base, only=None):
    manifest_path = OUTPUT / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text())["screenshots"]
        if only and manifest_path.exists() else {}
    )
    failures = []
    OUTPUT.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=[
            "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
        ])

        async def new_page(*, authenticated=True, verified=True, width=1440, height=960,
                           settings=None, delivery="verified"):
            context = await browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=1.5, locale="ko-KR", timezone_id="Asia/Seoul",
                color_scheme="light", permissions=["microphone"],
            )
            state = {"verified": verified, "delivery": delivery, "records": records()}
            initial = {"ttsAutoPlay": False, "autoSummarizeMessageCount": 10,
                       "sourceLang": "en", "translationTarget": "ko", **(settings or {})}
            config = {"settings": initial, "client": CLIENT, "authenticated": authenticated,
                      "id": token(verified), "access": token(verified, True), "summary": SUMMARY}
            await context.add_init_script(SOCKET)
            await context.add_init_script("""(cfg => {
              if (window !== window.top) return;
              localStorage.setItem('transmeet-settings', JSON.stringify(cfg.settings));
              localStorage.setItem('transmeet-theme', JSON.stringify({mode:'light',
                accent:'cyan',font:'default',scale:100,lineSpacing:'normal'}));
              localStorage.setItem('transmeet-summary-width','420');
              window.__guideSummary = cfg.summary;
              if (!cfg.authenticated) return;
              const p = `CognitoIdentityServiceProvider.${cfg.client}`;
              localStorage.setItem(`${p}.LastAuthUser`, 'guide-user');
              for (const [k,v] of Object.entries({idToken:cfg.id,accessToken:cfg.access,
                refreshToken:'documentation-preview-only',clockDrift:'0'}))
                localStorage.setItem(`${p}.guide-user.${k}`, v);
            })""" + f"({json.dumps(config, ensure_ascii=False)})")
            page = await context.new_page()
            page.set_default_timeout(10000)
            page.on("pageerror", lambda error: failures.append(str(error)))
            templates = [{
                "templateId": "guide-weekly", "name": "주간 팀 회의", "kind": "instruction",
                "body": "이번 주 진행 상황, 결정 사항, 다음 할 일을 정리해 주세요.",
                "createdAt": f"{DAY}T09:00:00Z", "updatedAt": f"{DAY}T09:00:00Z",
            }]

            async def route_request(route):
                url = urlparse(route.request.url)
                method = route.request.method
                result = {}
                if url.path.startswith("/__guide_api"):
                    path = url.path.removeprefix("/__guide_api")
                    if path == "/meetings" and method == "GET":
                        result = list(state["records"].values())
                    elif path == "/meetings" and method == "POST":
                        result = {"meetingId": "guide-new", "title": "새 회의",
                                  "createdAt": f"{DAY}T09:30:00Z", "messages": [], "messageCount": 0}
                        state["records"]["guide-new"] = result
                    elif path.startswith("/meetings/") and method == "GET":
                        result = state["records"][path.split("/")[2]]
                    elif path.endswith("/summary-style") and method == "PUT":
                        body = route.request.post_data_json
                        result = {"meetingId": path.split("/")[2], **body}
                    elif path == "/me/summary-templates" and method == "GET":
                        result = {"templates": templates}
                    elif path == "/me/summary-templates" and method == "POST":
                        result = {"templateId": "guide-new-template", **route.request.post_data_json,
                                  "createdAt": f"{DAY}T09:00:00Z", "updatedAt": f"{DAY}T09:00:00Z"}
                        templates.append(result)
                    elif path in {"/me/email", "/me/email/verify"}:
                        if method == "POST":
                            state["delivery"] = "pending"
                        result = {"email": "guide@example.com", "status": state["delivery"]}
                    elif path == "/tts":
                        # A short silent WAV is enough for the preview's enabled playback state.
                        wav = b"RIFF" + (36 + 1600).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
                        wav += b"\x01\x00\x01\x00" + (16000).to_bytes(4, "little") + (32000).to_bytes(4, "little")
                        wav += b"\x02\x00\x10\x00data" + (1600).to_bytes(4, "little") + bytes(1600)
                        result = {"audioData": base64.b64encode(wav).decode(),
                                  "translatedText": "Could you explain that one more time?",
                                  "format": "wav"}
                    elif path.endswith("/summarize"):
                        result = {"meetingId": path.split("/")[2], "summary": SUMMARY}
                    elif method == "DELETE":
                        await route.fulfill(status=204)
                        return
                    else:
                        failures.append(f"Unhandled fixture API: {method} {path}")
                    await route.fulfill(content_type="application/json", body=json.dumps(result))
                elif url.hostname and url.hostname.startswith("cognito-idp."):
                    operation = route.request.headers.get("x-amz-target", "").split(".")[-1]
                    if operation == "GetUser":
                        result = {"Username": "guide-user", "UserAttributes": [
                            {"Name": "email", "Value": "guide@example.com"},
                            {"Name": "email_verified", "Value": str(state["verified"]).lower()},
                        ]}
                    elif operation == "GetUserAttributeVerificationCode":
                        result = {"CodeDeliveryDetails": {"Destination": "guide@example.com",
                                  "DeliveryMedium": "EMAIL", "AttributeName": "email"}}
                    elif operation == "VerifyUserAttribute":
                        state["verified"] = True
                    elif operation == "InitiateAuth":
                        result = {"AuthenticationResult": {"IdToken": token(state["verified"]),
                                  "AccessToken": token(state["verified"], True),
                                  "ExpiresIn": 3600, "TokenType": "Bearer"}}
                    elif operation == "ForgotPassword":
                        result = {"CodeDeliveryDetails": {"Destination": "guide@example.com",
                                  "DeliveryMedium": "EMAIL", "AttributeName": "email"}}
                    elif operation in {"ConfirmForgotPassword", "ChangePassword"}:
                        result = {}
                    else:
                        failures.append(f"Unhandled fixture auth: {operation}")
                    await route.fulfill(content_type="application/x-amz-json-1.1", body=json.dumps(result))
                elif url.path.startswith("/p/"):
                    await route.fulfill(content_type="text/html", body=PAGE_HTML)
                elif url.path.endswith("audio-timer.worker.ts"):
                    await route.fulfill(content_type="text/javascript",
                                        body="onmessage = () => {};")
                elif url.hostname in {"127.0.0.1", "localhost"}:
                    await route.continue_()
                else:
                    failures.append(f"Blocked external request: {url.scheme}://{url.hostname}{url.path}")
                    await route.abort()

            await context.route("**/*", route_request)
            await page.goto(base + "/?meeting=guide-meeting", wait_until="networkidle")
            if authenticated:
                await expect(page.get_by_role("button", name="녹음 시작", exact=True)).to_be_visible()
                await page.wait_for_function("window.__guideSockets.some(s => s.readyState === 1)")
            return page, context, state

        async def shot(page, name, caption, alt, *, scope=None, pins=()):
            if only and name not in only:
                return
            await page.evaluate("document.fonts.ready")
            await page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
            root_box = await scope.bounding_box() if scope else {
                "x": 0, "y": 0, **page.viewport_size,
            }
            assert root_box, name
            points = []
            for number, locator, label in pins:
                box = await locator.bounding_box()
                assert box, f"{name}: missing pin {label}"
                # Clamp to the visible part of the chosen control in the crop.
                x = max(10, min(root_box["width"] - 10,
                               box["x"] - root_box["x"] + box["width"] / 2))
                y = max(10, min(root_box["height"] - 10,
                               box["y"] - root_box["y"] + box["height"] / 2))
                left = max(0, box["x"] - root_box["x"])
                top = max(0, box["y"] - root_box["y"])
                points.append({
                    "number": number, "x": round(100 * x / root_box["width"], 3),
                    "y": round(100 * y / root_box["height"], 3), "label": label,
                    "rect": {
                        "left": round(100 * left / root_box["width"], 3),
                        "top": round(100 * top / root_box["height"], 3),
                        "width": round(100 * min(box["width"], root_box["width"] - left) / root_box["width"], 3),
                        "height": round(100 * min(box["height"], root_box["height"] - top) / root_box["height"], 3),
                    },
                })
            raw = await (scope.screenshot(animations="disabled") if scope
                         else page.screenshot(animations="disabled"))
            image = Image.open(io.BytesIO(raw)).convert("RGB")
            image.save(OUTPUT / f"{name}.webp", format="WEBP", quality=88, method=6)
            manifest[name] = {"file": f"{name}.webp", "caption": caption, "alt": alt,
                              "width": image.width, "height": image.height, "pins": points}
            print(f"Captured {name}", flush=True)

        page, context, _ = await new_page(authenticated=False, width=1000, height=720)
        await shot(page, "login", "이메일과 비밀번호로 로그인",
                   "TransMeet 로그인 화면. 이메일, 비밀번호 입력칸과 로그인 버튼.",
                   pins=[(1, page.get_by_label("이메일", exact=True), "이메일"),
                         (2, page.get_by_label("비밀번호", exact=True), "비밀번호"),
                         (3, page.get_by_role("button", name="로그인", exact=True), "로그인")])
        await page.get_by_role("button", name="비밀번호 찾기", exact=True).click()
        await page.get_by_role("textbox", name="이메일", exact=True).fill("guide@example.com")
        await shot(page, "password-reset-start", "비밀번호 찾기 → 인증 코드 받기",
                   "비밀번호 찾기 화면의 초대받은 이메일 입력, 인증 코드 받기와 로그인으로 돌아가기.",
                   scope=page.locator("section"),
                   pins=[(1, page.get_by_role("textbox", name="이메일", exact=True), "계정 이메일"),
                         (2, page.get_by_role("button", name="인증 코드 받기", exact=True), "인증 코드 받기")])
        await page.get_by_role("button", name="인증 코드 받기", exact=True).click()
        await expect(page.get_by_role("button", name="비밀번호 재설정", exact=True)).to_be_visible()
        await page.set_viewport_size({"width": 1000, "height": 1000})
        await shot(page, "password-reset-code", "메일의 인증 코드와 새 비밀번호 입력",
                   "비밀번호 재설정 화면. 인증 코드, 새 비밀번호, 새 비밀번호 확인, 비밀번호 재설정과 코드 다시 받기.",
                   scope=page.locator("section"))
        await page.get_by_label("인증 코드", exact=True).fill("123456")
        await page.get_by_label("새 비밀번호", exact=True).fill("GuideExample2026")
        await page.get_by_label("새 비밀번호 확인", exact=True).fill("GuideExample2026")
        await page.get_by_role("button", name="비밀번호 재설정", exact=True).click()
        await expect(page.get_by_text("비밀번호가 재설정되었습니다. 새 비밀번호로 로그인해 주세요.")).to_be_visible()
        await context.close()

        page, context, state = await new_page()
        await shot(page, "workspace", "메뉴 위치를 한눈에 확인하세요",
                   "왼쪽 회의 목록, 가운데 대화, 오른쪽 내 메모·요약·회의 페이지, 상단 언어와 설정, 하단 녹음 버튼.",
                   pins=[
                       (1, page.get_by_role("button", name="새 회의", exact=True), "회의 목록 · 새 회의"),
                       (2, page.get_by_role("combobox", name="음성 언어", exact=True), "음성 언어 · 번역 대상"),
                       (3, page.locator("#meeting-voice-panel"), "대화"),
                       (4, page.get_by_role("tablist", name="회의 도구", exact=True), "내 메모 · 요약 · 회의 페이지"),
                       (5, page.get_by_role("button", name="녹음 시작", exact=True), "녹음 시작"),
                       (6, page.get_by_role("button", name="설정", exact=True), "설정"),
                   ])
        await shot(page, "languages", "입력 언어와 번역 대상을 각각 선택",
                   "상단의 입력 · English, 번역 · 한국어 드롭다운과 감지 전 표시.",
                   scope=page.get_by_role("combobox", name="음성 언어", exact=True).locator("xpath=../.."),
                   pins=[(1, page.get_by_role("combobox", name="음성 언어", exact=True), "실제로 말하는 언어"),
                         (2, page.get_by_role("combobox", name="번역 대상", exact=True), "읽고 싶은 언어")])
        await page.get_by_role("button", name=TITLE + " 작업", exact=True).click()
        await shot(page, "meeting-menu", "회의 제목 옆 ··· 에서 기록 관리",
                   "회의 목록의 작업 메뉴. 제목 편집, 제목 자동 생성, 회의 삭제.",
                   scope=page.get_by_role("complementary", name="회의 목록", exact=True),
                   pins=[(1, page.get_by_role("textbox", name="회의 검색", exact=True), "제목으로 검색"),
                         (2, page.get_by_role("button", name="제목 편집", exact=True), "제목 편집"),
                         (3, page.get_by_role("button", name="제목 자동 생성", exact=True), "제목 자동 생성"),
                         (4, page.get_by_role("button", name="회의 삭제", exact=True), "회의 삭제")])
        await page.get_by_role("button", name="회의 삭제", exact=True).click()
        await shot(page, "meeting-delete", "삭제 전 회의 제목을 확인",
                   "회의 삭제 확인. 선택한 회의 제목, 취소 버튼과 삭제 버튼.",
                   scope=page.get_by_role("group", name="회의 삭제 확인", exact=True))
        await page.get_by_role("button", name="취소", exact=True).click()
        await shot(page, "conversation", "원문과 번역, 화자와 시간을 함께 확인",
                   "실제 대화 패널. 화자별 영문 원문과 한국어 번역, 다시 번역, 원문 음성 재생, 대화 복사와 글자 크기.",
                   scope=page.locator("#meeting-voice-panel"),
                   pins=[(1, page.get_by_role("button", name="대화 복사", exact=True), "전체 원문 복사"),
                         (2, page.get_by_role("combobox", name="대화 글자 크기"), "글자 크기"),
                         (3, page.get_by_role("button", name=re.compile("화자 1.*다시 번역")).first, "다시 번역"),
                         (4, page.get_by_role("button", name=re.compile("화자 1.*원문 음성 재생")).first, "원문 음성 재생")])
        await page.get_by_role("button", name="녹음 시작", exact=True).click()
        await expect(page.get_by_role("button", name="녹음 일시정지", exact=True)).to_be_visible()
        await shot(page, "recording", "녹음 중에는 일시정지와 회의 종료가 나란히 표시",
                   "녹음 중인 대화 패널의 일시정지 버튼, 빨간 회의 종료 버튼, 더보기 버튼.",
                   scope=page.get_by_role("group", name="녹음 조작", exact=True),
                   pins=[(1, page.get_by_role("button", name="녹음 일시정지", exact=True), "잠시 멈추기"),
                         (2, page.get_by_role("button", name="회의 종료", exact=True), "회의 정리하기")])
        await page.get_by_role("button", name="녹음 일시정지", exact=True).click()
        await shot(page, "paused", "일시정지 후에는 녹음 재개",
                   "일시정지 상태의 녹음 재개와 회의 종료 버튼.",
                   scope=page.get_by_role("group", name="녹음 조작", exact=True))
        await page.get_by_role("button", name="녹음 작업 더보기", exact=True).click()
        await shot(page, "audio-menu", "··· 메뉴에서 녹음 다운로드",
                   "녹음 작업 더보기 메뉴의 녹음 다운로드 버튼.",
                   scope=page.get_by_role("group", name="녹음 작업", exact=True))
        await page.keyboard.press("Escape")
        # Inactive tabs include the accessible badge text "새 내용".
        await page.get_by_role("tab", name=re.compile("^내 메모")).click()
        await page.get_by_role("textbox", name="내 메모 입력", exact=True).fill("출시 일정은 어떻게 결정됐나요?")
        await shot(page, "notes", "한글 메모를 입력하고 보내기",
                   "내 메모 탭의 한국어 입력과 영어 번역, 음성 재생 버튼, 입력창과 보내기 버튼.",
                   scope=page.get_by_role("complementary", name="회의 도구", exact=True),
                   pins=[(1, page.get_by_role("textbox", name="내 메모 입력", exact=True), "한국어로 입력"),
                         (2, page.get_by_role("button", name="메모 보내기", exact=True), "보내기"),
                         (3, page.get_by_role("button", name="내 메시지 음성 재생", exact=True), "번역 음성 재생")])
        await page.get_by_role("button", name="메모 보내기", exact=True).click()
        await expect(page.get_by_text("11월 6일 출시를 유지", exact=False).last).to_be_visible()
        await shot(page, "notes-question", "대화에 관한 질문의 답변 예시",
                   "내 메모에서 출시 일정에 관해 질문한 뒤, 번역문 아래에 대화 맥락을 바탕으로 표시된 답변 예시.",
                   scope=page.get_by_role("complementary", name="회의 도구", exact=True))
        await page.get_by_role("button", name="빠른 번역", exact=True).click()
        await page.get_by_role("textbox", name="번역할 내용", exact=True).fill("다시 한번 설명해 주시겠어요?")
        await page.get_by_role("button", name="번역하기", exact=True).click()
        await expect(page.get_by_role("button", name="음성 재생", exact=True)).to_be_enabled()
        await shot(page, "quick-translate", "회의 입력과 별도로 짧은 문장을 빠르게 번역",
                   "빠른 번역 창의 두 번역 방향, 번역할 내용, 번역하기, 결과 음성 재생과 복사, 최근 번역.",
                   scope=page.get_by_role("dialog"),
                   pins=[(1, page.get_by_role("group", name="빠른 번역 방향", exact=True), "방향 선택"),
                         (2, page.get_by_role("button", name="번역하기", exact=True), "번역하기"),
                         (3, page.get_by_role("button", name="음성 재생", exact=True), "음성 재생"),
                         (4, page.get_by_role("button", name="전체 삭제", exact=True), "최근 번역 관리")])
        await page.get_by_role("button", name="빠른 번역 닫기", exact=True).click()
        await page.get_by_role("tab", name="요약", exact=True).click()
        await shot(page, "summary", "요약 방식을 선택한 뒤 새로 생성",
                   "요약 탭. 요약 방식 선택, 요약 복사, MD, 새로 생성, 생성된 요약 방식과 내용.",
                   scope=page.get_by_role("complementary", name="회의 도구", exact=True),
                   pins=[(1, page.get_by_role("combobox", name="요약 방식", exact=True), "요약 방식 선택"),
                         (2, page.get_by_role("button", name="요약 새로 생성", exact=True), "새로 생성"),
                         (3, page.get_by_role("button", name="요약 복사", exact=True), "서식 복사"),
                         (4, page.get_by_role("button", name="마크다운 복사", exact=True), "마크다운 복사")])
        await page.get_by_role("tab", name="회의 페이지", exact=True).click()
        await shot(page, "page-create", "페이지 모양을 고르고 만들기",
                   "회의 페이지 탭. 시각 회의록과 상세 회의록 선택 및 페이지 만들기 버튼.",
                   scope=page.get_by_role("complementary", name="회의 도구", exact=True),
                   pins=[(1, page.get_by_role("group", name="페이지 모양", exact=True), "페이지 모양"),
                         (2, page.get_by_role("button", name="페이지 만들기", exact=True), "페이지 만들기")])
        await page.get_by_role("button", name="페이지 만들기", exact=True).click()
        await page.evaluate("""() => window.__guideSockets.findLast(s => s.readyState === 1).emit({
          type:'page_stream',phase:'status',started:true,meetingId:'guide-meeting',
          text:'대화와 결정 사항을 정리하고 있습니다.'
        })""")
        await expect(page.get_by_text("대화와 결정 사항을 정리하고 있습니다.", exact=True)).to_be_visible()
        await shot(page, "page-generating", "진행 상황 확인",
                   "페이지 생성 중 표시, 경과 시간과 작업 진행 내역.",
                   scope=page.get_by_role("complementary", name="회의 도구", exact=True))
        meeting_page = {"pageId": "guide-page", "url": base + "/p/guide-meeting/example.html",
                        "title": TITLE, "generatedAt": f"{DAY}T09:10:00Z",
                        "shareSummary": "11월 6일 출시 및 가이드 보강", "style": "visual"}
        state["records"]["guide-meeting"]["page"] = meeting_page
        await page.evaluate("""p => window.__guideSockets.findLast(s => s.readyState === 1).emit({
          type:'page_stream',phase:'done',meetingId:'guide-meeting',page:p
        })""", meeting_page)
        await expect(page.get_by_role("button", name="공유", exact=True)).to_be_visible()
        await expect(page.get_by_role("button", name="서식 복사", exact=True)).to_be_visible()
        await shot(page, "page-share", "미리보기 후 링크 공유",
                   "회의 페이지 미리보기 예제. 공유, 서식 복사, MD, 새 탭, 다시 만들기, 공유 해제.",
                   scope=page.get_by_role("complementary", name="회의 도구", exact=True),
                   pins=[(1, page.get_by_role("button", name="공유", exact=True), "공유 / 링크 복사"),
                         (2, page.get_by_role("link", name="새 탭", exact=False), "새 탭에서 열기"),
                         (3, page.get_by_role("button", name="다시 만들기", exact=True), "새 페이지 생성"),
                         (4, page.get_by_role("button", name="공유 해제", exact=True), "공유 링크 해제")])
        await page.get_by_role("button", name="다시 만들기", exact=True).click()
        await shot(page, "page-regenerate", "다시 만들면 기존 공유 링크가 만료",
                   "페이지 다시 만들기 확인 화면의 페이지 모양, 기존 링크 만료 안내, 취소와 다시 만들기.",
                   scope=page.get_by_role("group", name="회의 페이지 작업", exact=True))
        await page.get_by_role("button", name="취소", exact=True).click()
        await page.get_by_role("button", name="설정", exact=True).click()
        await shot(page, "settings-general", "설정 → 음성 · 번역",
                   "음성·번역 설정의 오디오 입력, 번역 모델, 번역 시점, 음성 자동 재생, 목소리, 자동 요약 주기, 종료 메일.",
                   scope=page.get_by_role("dialog"),
                   pins=[(1, page.get_by_role("group", name="오디오 입력", exact=True), "오디오 입력"),
                         (2, page.get_by_role("group", name="번역 시점", exact=True), "번역 시점"),
                         (3, page.get_by_role("switch", name="번역 음성 자동 재생", exact=True), "음성 자동 재생"),
                         (4, page.get_by_role("group", name="자동 요약 주기", exact=True), "자동 요약")])
        await page.get_by_role("tab", name="테마", exact=True).click()
        await shot(page, "settings-theme", "설정 → 테마",
                   "화면 모드, 포인트 색상, 글꼴, 화면 크기, 말풍선 크기, 줄 간격, 미리보기와 테마 초기화.",
                   scope=page.get_by_role("dialog"))
        await page.get_by_role("tab", name="요약 양식", exact=True).click()
        await shot(page, "templates-list", "설정 → 요약 양식 → 내 양식",
                   "저장된 주간 팀 회의 양식과 수정, 삭제, 새 양식 버튼.",
                   scope=page.get_by_role("dialog"))
        await page.get_by_role("button", name="새 양식", exact=True).click()
        await page.get_by_role("textbox", name="양식 이름", exact=True).fill("프로젝트 점검 회의")
        await page.get_by_role("textbox", name="양식 내용", exact=True).fill(
            "다음 순서로 정리해 주세요.\n1. 이번 주 진행 상황\n2. 결정 사항\n3. 다음 할 일: 담당 / 기한 / 작업\n4. 아직 정하지 못한 내용\n\n대화에 없는 담당자나 날짜는 '미정'으로 표시해 주세요."
        )
        await shot(page, "templates-editor", "이름과 내용을 입력한 뒤 저장",
                   "새 요약 양식 작성 화면. 양식 이름, 지시문 작성 또는 기존 회의록 붙여넣기, 양식 내용과 저장 버튼.",
                   scope=page.get_by_role("dialog"),
                   pins=[(1, page.get_by_role("textbox", name="양식 이름", exact=True), "이름"),
                         (2, page.get_by_role("button", name="지시문 작성", exact=True), "만드는 방법"),
                         (3, page.get_by_role("textbox", name="양식 내용", exact=True), "원하는 요약 형식"),
                         (4, page.get_by_role("button", name="저장", exact=True), "저장")])
        await page.get_by_role("button", name="설정 닫기", exact=True).click()
        await page.get_by_role("button", name="설정", exact=True).click()
        await page.get_by_text("고급 설정", exact=True).click()
        await page.locator("#settings-content").evaluate("(el) => el.scrollTop = el.scrollHeight")
        await shot(page, "settings-advanced", "고급 설정 펼치기 · Transcribe",
                   "Transcribe의 음성 인식 엔진, 발화 중 번역, 무음 후 대화 나누기와 말풍선당 최대 문장.",
                   scope=page.get_by_role("dialog"))
        await page.get_by_role("button", name="일정 간격", exact=True).click()
        await page.locator("#settings-content").evaluate("(el) => el.scrollTop = el.scrollHeight")
        await shot(page, "settings-interval", "일정 간격을 선택하면 번역 간격 표시",
                   "발화 중 번역 일정 간격 모드에서 0.5초, 1초, 1.5초, 2초, 3초 선택.",
                   scope=page.get_by_role("dialog"))
        await page.evaluate("window.__guideStreaming = false")
        await page.get_by_role("button", name="Whisper", exact=True).click()
        await page.wait_for_timeout(150)
        await page.locator("#settings-content").evaluate("(el) => el.scrollTop = el.scrollHeight")
        await shot(page, "settings-batch", "구간 처리 방식에서만 보이는 음성 처리 구간",
                   "Whisper 구간 처리 설정의 2초, 4초, 6초, 8초, 10초 선택. 연결한 엔진 방식에 따라 표시가 달라짐.",
                   scope=page.get_by_role("dialog"))
        await page.evaluate("window.__guideStreaming = true")
        await page.get_by_role("button", name="Qwen3-ASR", exact=True).click()
        await page.wait_for_timeout(150)
        await page.locator("#settings-content").evaluate("(el) => el.scrollTop = el.scrollHeight")
        await shot(page, "settings-streaming", "스트리밍 엔진의 준비 상태 확인",
                   "Qwen3-ASR 스트리밍 선택 후 사용 가능 상태와 확정된 발화 기준 번역 안내.",
                   scope=page.get_by_role("dialog"))
        await context.close()

        page, context, _ = await new_page(verified=False, settings={"emailOnEnd": False}, delivery="none")
        await page.get_by_role("button", name="설정", exact=True).click()
        await page.get_by_role("switch", name="회의 종료 시 메일로 받기", exact=True).click()
        await expect(page.get_by_role("textbox", name="이메일 인증 코드", exact=True)).to_be_visible()
        await page.locator("#settings-content").evaluate("(el) => el.scrollTop = el.scrollHeight")
        await shot(page, "email-code", "메일 받기 ① 계정 이메일 코드 확인",
                   "메일로 받기 설정을 켠 뒤 이메일 인증 코드 입력, 코드 확인, 인증 코드 다시 보내기.",
                   scope=page.get_by_role("dialog"))
        await page.get_by_role("textbox", name="이메일 인증 코드", exact=True).fill("123456")
        await page.get_by_role("button", name="코드 확인", exact=True).click()
        await expect(page.get_by_role("button", name="인증 상태 확인", exact=True)).to_be_visible()
        await page.locator("#settings-content").evaluate("(el) => el.scrollTop = el.scrollHeight")
        await shot(page, "email-link", "메일 받기 ② 수신 인증 메일의 링크 확인",
                   "수신 인증 대기 상태. 메일 링크를 누르라는 안내, 인증 상태 확인 및 인증 메일 다시 보내기.",
                   scope=page.get_by_role("dialog"))
        await context.close()

        page, context, _ = await new_page(settings={"emailOnEnd": True})
        await page.get_by_role("button", name="설정", exact=True).click()
        await expect(page.get_by_text("인증 완료 — 회의 종료 후 메일을 보냅니다.", exact=True)).to_be_visible()
        await page.locator("#settings-content").evaluate("(el) => el.scrollTop = el.scrollHeight")
        await shot(page, "email-ready", "인증 완료를 확인하면 준비 끝",
                   "예제 계정 guide@example.com과 인증 완료 — 회의 종료 후 메일을 보냅니다 표시.",
                   scope=page.get_by_role("dialog"))
        await page.get_by_role("tab", name="계정", exact=True).click()
        await expect(page.get_by_text("비밀번호를 잊었을 때 이 이메일로 인증 코드를 받습니다.", exact=True)).to_be_visible()
        await shot(page, "account-settings", "설정 → 계정 → 비밀번호 변경",
                   "계정 설정의 인증된 이메일, 현재 비밀번호와 새 비밀번호, 새 비밀번호 확인, 비밀번호 변경 버튼.",
                   scope=page.get_by_role("dialog"),
                   pins=[(1, page.get_by_label("현재 비밀번호", exact=True), "현재 비밀번호"),
                         (2, page.get_by_label("새 비밀번호", exact=True), "새 비밀번호"),
                         (3, page.get_by_label("새 비밀번호 확인", exact=True), "한 번 더 입력"),
                         (4, page.get_by_role("button", name="비밀번호 변경", exact=True), "변경")])
        await page.get_by_label("현재 비밀번호", exact=True).fill("GuidePrevious2026")
        await page.get_by_label("새 비밀번호", exact=True).fill("GuideNextExample2026")
        await page.get_by_label("새 비밀번호 확인", exact=True).fill("GuideNextExample2026")
        await page.get_by_role("button", name="비밀번호 변경", exact=True).click()
        await expect(page.get_by_text("비밀번호가 변경되었습니다. 다음 로그인부터 새 비밀번호를 사용해 주세요.", exact=True)).to_be_visible()
        await context.close()

        page, context, _ = await new_page(verified=False)
        await page.get_by_role("button", name="설정", exact=True).click()
        await page.get_by_role("tab", name="계정", exact=True).click()
        await page.get_by_role("button", name="인증 코드 받기", exact=True).click()
        await expect(page.get_by_role("button", name="이메일 인증", exact=True)).to_be_visible()
        await shot(page, "account-email", "계정 이메일 인증을 미리 완료하세요",
                   "계정 탭에서 이메일 인증이 필요한 상태. 이메일 인증 코드 입력, 이메일 인증, 코드 다시 받기.",
                   scope=page.get_by_role("dialog"),
                   pins=[(1, page.get_by_label("이메일 인증 코드", exact=True), "6자리 코드"),
                         (2, page.get_by_role("button", name="이메일 인증", exact=True), "인증 확인")])
        await context.close()

        page, context, _ = await new_page(width=390, height=844)
        await shot(page, "mobile", "모바일에서는 네 개의 탭으로 이동",
                   "모바일 TransMeet 화면. 왼쪽 회의 목록 버튼, 오른쪽 설정, 대화·내 메모·요약·회의 페이지 탭.",
                   pins=[(1, page.get_by_role("button", name="회의 목록 열기", exact=True), "회의 목록"),
                         (2, page.get_by_role("tablist", name="회의 화면", exact=True), "화면 이동"),
                         (3, page.get_by_role("button", name="설정", exact=True), "빠른 설정")])
        await page.get_by_role("button", name="설정", exact=True).click()
        await shot(page, "mobile-settings", "모바일 설정 → 전체 설정",
                   "모바일 빠른 설정 창. 음성 및 번역 언어, 어두운 화면으로 전환, 전체 설정, 로그아웃.",
                   pins=[(1, page.get_by_role("button", name="전체 설정", exact=True), "전체 설정 열기")])
        await context.close()
        await browser.close()

    (OUTPUT / "manifest.json").write_text(json.dumps({
        "captured": DAY, "source": "Actual src/app/page.tsx and components",
        "data": "Invented meeting, account and AI responses; all external services intercepted",
        "screenshots": manifest,
    }, ensure_ascii=False, indent=2) + "\n")
    if failures:
        raise AssertionError("\n".join(failures))
    print(f"Saved {len(manifest)} screenshots; no unhandled service requests or browser errors.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="Comma-separated screenshot names to update; keeps the rest")
    args = parser.parse_args()
    only = set(args.only.split(",")) if args.only else None
    with tempfile.TemporaryDirectory(prefix="transmeet-guide-") as temp:
        directory = Path(temp) / "preview"
        directory.mkdir()
        build_preview(directory)
        handler = functools.partial(QuietHandler, directory=str(directory))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            asyncio.run(run_capture(f"http://127.0.0.1:{server.server_port}", only))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
