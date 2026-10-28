"""Exercise real notification UI/SDK code with intercepted AWS and email APIs.

Run: python3 tests/email_verification_browser.py [scenario ...]
Builds a temporary fixture; no dev server, real account, or outbound mail is used.
"""
import asyncio
import base64
import functools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path(os.environ.get("EMAIL_TEST_ARTIFACTS", "/tmp/transmeet-email-verification"))
EMAIL = "person@example.com"
CLIENT_ID = "email-test-client"


def token(verified, *, access=False):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return ".".join([
        encode({"alg": "none"}),
        encode({
            "sub": "user-a", "cognito:username": "user-a", "email": EMAIL,
            "email_verified": verified, "iat": int(time.time()), "exp": int(time.time()) + 3600,
            "token_use": "access" if access else "id",
        }),
        "test-signature",
    ])


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def build_fixture(directory):
    build = r"""
const fs = require('node:fs');
const path = require('node:path');
const config = JSON.parse(fs.readFileSync(0, 'utf8'));
require('./infra/node_modules/esbuild').buildSync({
  entryPoints: ['tests/email-settings.fixture.tsx'],
  bundle: true, outfile: path.join(config.directory, 'app.js'), jsx: 'automatic',
  alias: {'@': path.join(process.cwd(), 'src')},
  define: {
    'process.env.NODE_ENV': '"production"',
    'process.env.NEXT_PUBLIC_API_ENDPOINT': '"/__api"',
    'process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID': '"us-east-1_TESTPOOL"',
    'process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID': '"email-test-client"',
    'process.env.NEXT_PUBLIC_WS_ENDPOINT': '"wss://fixture.invalid/ws"',
    global: 'globalThis',
  },
});
"""
    subprocess.run(
        ["node", "-e", build], cwd=ROOT, input=json.dumps({"directory": str(directory)}),
        text=True, check=True, capture_output=True,
    )
    subprocess.run(
        ["node", "node_modules/tailwindcss/lib/cli.js", "-i", "src/app/globals.css",
         "-o", str(directory / "styles.css")],
        cwd=ROOT, check=True, capture_output=True,
    )
    (directory / "index.html").write_text(
        '<!doctype html><html lang="ko"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<link rel="stylesheet" href="/styles.css"><title>Email settings test</title>'
        '<body class="bg-canvas"><div id="root"></div><script src="/app.js"></script></body></html>'
    )


async def scenario(browser, base, name):
    verified = name in {"ready", "pending", "poll", "retry"}
    state = {
        "verified": verified,
        "delivery": "verified" if name == "ready" else "pending" if name in {"pending", "poll"} else "none",
        "get_user": 0, "code_requests": 0, "delivery_requests": 0, "fail_delivery": name == "retry",
    }
    gate = asyncio.Event()
    if name not in {"race", "cancel", "unmount"}:
        gate.set()
    lookup_started = asyncio.Event()
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900}, service_workers="block",
    )
    config = {
        "client": CLIENT_ID, "id": token(verified), "access": token(verified, access=True),
        "holdSocket": name == "connecting",
    }
    await context.add_init_script("""(config => {
      const prefix = `CognitoIdentityServiceProvider.${config.client}`;
      localStorage.setItem(`${prefix}.LastAuthUser`, 'user-a');
      for (const [key, value] of Object.entries({
        idToken: config.id, accessToken: config.access, refreshToken: 'refresh-token', clockDrift: '0'
      })) localStorage.setItem(`${prefix}.user-a.${key}`, value);
      localStorage.setItem('transmeet-settings', JSON.stringify({emailOnEnd:false}));
      window.__messages = [];
      window.__sockets = [];
      class FakeWebSocket {
        static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
        constructor() {
          this.readyState = 0;
          window.__sockets.push(this);
          if (!config.holdSocket) setTimeout(() => this.open(), 0);
        }
        open() { this.readyState = 1; this.onopen?.(); }
        send(value) { window.__messages.push(JSON.parse(value)); }
        close(code = 1000) { this.readyState = 3; this.onclose?.({code}); }
      }
      window.WebSocket = FakeWebSocket;
    })""" + f"({json.dumps(config)})")
    page = await context.new_page()
    page.set_default_timeout(7000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    async def cognito(route):
        operation = route.request.headers.get("x-amz-target", "").split(".")[-1]
        body = route.request.post_data_json
        status = 200
        if operation == "GetUser":
            state["get_user"] += 1
            lookup_started.set()
            if name in {"cancel", "unmount"} or (name == "race" and state["get_user"] == 1):
                await gate.wait()
            result = {
                "Username": "user-a",
                "UserAttributes": [
                    {"Name": "email", "Value": EMAIL},
                    {"Name": "email_verified", "Value": str(state["verified"]).lower()},
                ],
            }
        elif operation == "GetUserAttributeVerificationCode":
            assert body["AttributeName"] == "email"
            state["code_requests"] += 1
            result = {"CodeDeliveryDetails": {"Destination": EMAIL, "DeliveryMedium": "EMAIL", "AttributeName": "email"}}
        elif operation == "VerifyUserAttribute":
            if body["Code"] == "123456":
                state["verified"] = True
                result = {}
            else:
                status = 400
                result = {
                    "__type": "ExpiredCodeException" if body["Code"] == "111111" else "CodeMismatchException",
                    "message": "Invalid verification code",
                }
        elif operation == "InitiateAuth":
            assert body["AuthFlow"] == "REFRESH_TOKEN_AUTH"
            result = {"AuthenticationResult": {
                "IdToken": token(state["verified"]),
                "AccessToken": token(state["verified"], access=True), "ExpiresIn": 3600,
                "TokenType": "Bearer",
            }}
        else:
            raise AssertionError(f"Unexpected Cognito operation: {operation}")
        await route.fulfill(status=status, content_type="application/x-amz-json-1.1", body=json.dumps(result))

    async def api(route):
        authorization = route.request.headers.get("authorization", "")
        claims = json.loads(base64.urlsafe_b64decode(authorization.split(".")[1] + "==")) if authorization else {}
        if not claims.get("email_verified"):
            await route.fulfill(status=400, content_type="application/json", body='{"error":"로그인 계정에 확인된 이메일이 없습니다"}')
            return
        if route.request.method == "POST":
            state["delivery_requests"] += 1
            if state["fail_delivery"]:
                state["fail_delivery"] = False
                await route.fulfill(status=500, content_type="application/json", body='{"error":"Temporary failure"}')
                return
            state["delivery"] = "pending"
        await route.fulfill(
            content_type="application/json",
            body=json.dumps({"email": EMAIL, "status": state["delivery"]}),
        )

    await page.route("https://cognito-idp.us-east-1.amazonaws.com/**", cognito)
    await page.route("**/__api/**", api)
    await page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
    await page.route("https://fonts.gstatic.com/**", lambda route: route.abort())
    try:
        await page.goto(base, wait_until="domcontentloaded")
        switch = page.get_by_role("switch", name="회의 종료 시 메일로 받기")
        await expect(switch).to_be_visible()
        if name in {"race", "cancel", "unmount"}:
            await asyncio.wait_for(lookup_started.wait(), timeout=7)
        if name == "other-tab":
            await page.wait_for_function("window.__messages.some(message => message.action === 'auth')")
            state["verified"] = True
            state["delivery"] = "verified"
            await page.evaluate("""config => {
              const prefix = `CognitoIdentityServiceProvider.${config.client}.user-a`;
              localStorage.setItem(`${prefix}.idToken`, config.id);
              localStorage.setItem(`${prefix}.accessToken`, config.access);
            }""", {"client": CLIENT_ID, "id": token(True), "access": token(True, access=True)})
        await switch.click()

        if name in {"cancel", "unmount"}:
            await expect(switch).to_have_attribute("aria-checked", "true")
            if name == "cancel":
                await switch.click()
            else:
                await page.get_by_role("button", name="설정 닫기", exact=True).click()
            gate.set()
            if name == "cancel":
                await expect(switch).to_have_attribute("aria-checked", "false")
            await page.wait_for_timeout(250)
            assert state["code_requests"] == state["delivery_requests"] == 0
        elif name in {"new", "race", "connecting", "expired"}:
            code = page.get_by_role("textbox", name="이메일 인증 코드")
            await expect(code).to_be_visible()
            await expect(page.get_by_text("인증 코드를 보냈습니다.", exact=False)).to_be_visible()
            assert state["code_requests"] == 1
            gate.set()
            if name == "race":
                await page.wait_for_timeout(100)
                assert state["code_requests"] == 1
            if name == "new":
                await page.screenshot(path=str(ARTIFACTS / "account-code.png"), full_page=True)
                await code.fill("000000")
                await page.get_by_role("button", name="코드 확인", exact=True).click()
                await expect(page.get_by_role("alert")).to_contain_text("인증 코드가")
            if name == "expired":
                await code.fill("111111")
                await page.get_by_role("button", name="코드 확인", exact=True).click()
                await expect(page.get_by_role("alert")).to_contain_text("만료")
                await page.get_by_role("button", name="인증 코드 다시 보내기", exact=True).click()
                await expect(page.get_by_text("인증 코드를 보냈습니다.", exact=False)).to_be_visible()
                assert state["code_requests"] == 2
            await code.fill("123456")
            await page.get_by_role("button", name="코드 확인", exact=True).click()
            await expect(page.get_by_text("수신 인증 메일을 보냈습니다.", exact=False)).to_be_visible()
            assert state["delivery_requests"] == 1
            if name == "connecting":
                await page.evaluate("window.__sockets[0].open()")
            messages = await page.evaluate("window.__messages")
            token_messages = [message for message in messages if message["action"] in {"auth", "refreshAuth"}]
            latest_claims = json.loads(base64.urlsafe_b64decode(token_messages[-1]["token"].split(".")[1] + "=="))
            assert latest_claims["email_verified"] is True
            assert await page.evaluate("window.__sockets.length") == 1
            if name == "connecting":
                assert messages[0]["action"] == "auth"
            if name == "new":
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.screenshot(path=str(ARTIFACTS / "delivery-pending-mobile.png"), full_page=True)
            state["delivery"] = "verified"
            if name == "new":
                await page.evaluate("window.dispatchEvent(new Event('focus'))")
            else:
                await page.get_by_role("button", name="인증 상태 확인", exact=True).click()
            await expect(page.get_by_text("인증 완료", exact=False)).to_be_visible()
            await expect(page.get_by_text("수신 인증 메일을 보냈습니다.", exact=False)).to_have_count(0)
            await page.get_by_role("button", name="회의 종료", exact=True).click()
            assert (await page.evaluate("window.__messages"))[-1]["emailOnEnd"] is True
        elif name in {"ready", "other-tab"}:
            await expect(page.get_by_text("인증 완료", exact=False)).to_be_visible()
            assert state["code_requests"] == state["delivery_requests"] == 0
            if name == "other-tab":
                messages = await page.evaluate("window.__messages")
                assert messages[-1]["action"] == "refreshAuth", messages
                claims = json.loads(base64.urlsafe_b64decode(messages[-1]["token"].split(".")[1] + "=="))
                assert claims["email_verified"] is True
                assert await page.evaluate("window.__sockets.length") == 1
        elif name in {"pending", "poll"}:
            await expect(page.get_by_role("button", name="인증 상태 확인", exact=True)).to_be_visible()
            assert state["delivery_requests"] == 0
            await page.get_by_role("button", name="설정 닫기", exact=True).click()
            await page.get_by_role("button", name="설정 열기", exact=True).click()
            await expect(page.get_by_role("button", name="인증 상태 확인", exact=True)).to_be_visible()
            state["delivery"] = "verified"
            if name == "pending":
                await page.evaluate("window.dispatchEvent(new Event('focus'))")
            await expect(page.get_by_text("인증 완료", exact=False)).to_be_visible(timeout=8000)
            assert state["delivery_requests"] == 0
        elif name == "retry":
            await expect(page.get_by_role("alert")).to_be_visible()
            await page.get_by_role("button", name="인증 메일 보내기", exact=True).click()
            await expect(page.get_by_text("수신 인증 메일을 보냈습니다.", exact=False)).to_be_visible()
            assert state["delivery_requests"] == 2
        assert not errors, errors
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        print(json.dumps({"scenario": name, "result": "passed", **state}), flush=True)
    except Exception:
        await page.screenshot(path=str(ARTIFACTS / f"failure-{name}.png"), full_page=True)
        print(json.dumps({"scenario": name, "state": state, "page_errors": errors}), flush=True)
        raise
    finally:
        gate.set()
        await context.close()


async def main(base):
    cases = sys.argv[1:] or ["new", "ready", "race", "cancel", "unmount", "pending", "poll", "retry", "expired", "connecting", "other-tab"]
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            for name in cases:
                await scenario(browser, base, name)
        finally:
            await browser.close()


if __name__ == "__main__":
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="transmeet-email-ui-") as temporary:
        directory = Path(temporary)
        build_fixture(directory)
        server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(directory)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            asyncio.run(main(f"http://127.0.0.1:{server.server_port}"))
        finally:
            server.shutdown()
