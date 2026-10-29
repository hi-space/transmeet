"""Exercise real account UI and Cognito SDK; intercept AWS to avoid real mail/accounts.

Run: python3 tests/auth_browser.py [change|recovery|verification|first-login|mobile]
Artifacts: /tmp/transmeet-auth-browser (override AUTH_TEST_ARTIFACTS).
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
ARTIFACTS = Path(os.environ.get("AUTH_TEST_ARTIFACTS", "/tmp/transmeet-auth-browser"))
EMAIL = "invited@example.com"
CLIENT_ID = "auth-test-client"


def token(verified=True, access=False):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return ".".join([
        encode({"alg": "none"}),
        encode({
            "sub": "auth-user", "cognito:username": "auth-user", "email": EMAIL,
            "email_verified": verified, "iat": int(time.time()), "exp": int(time.time()) + 3600,
            "token_use": "access" if access else "id",
        }),
        "test-signature",
    ])


def build_fixture(directory):
    script = r"""
const fs = require('node:fs'), path = require('node:path');
const config = JSON.parse(fs.readFileSync(0, 'utf8'));
require('./infra/node_modules/esbuild').buildSync({
  entryPoints: ['tests/auth.fixture.tsx'], bundle: true,
  outfile: path.join(config.directory, 'app.js'), jsx: 'automatic',
  alias: {'@': path.join(process.cwd(), 'src')},
  define: {
    'process.env.NODE_ENV': '"production"',
    'process.env.NEXT_PUBLIC_API_ENDPOINT': '""',
    'process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID': '"us-east-1_TESTPOOL"',
    'process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID': '"auth-test-client"',
    global: 'globalThis',
  },
});
"""
    subprocess.run(["node", "-e", script], cwd=ROOT,
                   input=json.dumps({"directory": str(directory)}), text=True,
                   check=True, capture_output=True)
    subprocess.run(["node", "node_modules/tailwindcss/lib/cli.js", "-i", "src/app/globals.css",
                    "-o", str(directory / "styles.css")],
                   cwd=ROOT, check=True, capture_output=True)
    (directory / "index.html").write_text(
        '<!doctype html><html lang="ko"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<link rel="stylesheet" href="/styles.css"><title>Account test</title>'
        '<body class="bg-canvas"><div id="root"></div><script src="/app.js"></script></body></html>'
    )


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


async def scenario(browser, base, name):
    account = name in {"change", "verification", "mobile"}
    state = {"verified": name != "verification", "calls": [], "changes": 0}
    context = await browser.new_context(
        viewport={"width": 375 if name == "mobile" else 1280, "height": 900},
        color_scheme="dark" if name == "mobile" else "light", service_workers="block",
    )
    if account:
        values = {
            "idToken": token(state["verified"]), "accessToken": token(state["verified"], True),
            "refreshToken": "refresh-token", "clockDrift": "0",
        }
        await context.add_init_script("""(values => {
          const prefix = 'CognitoIdentityServiceProvider.auth-test-client';
          localStorage.setItem(`${prefix}.LastAuthUser`, 'auth-user');
          for (const [key, value] of Object.entries(values))
            localStorage.setItem(`${prefix}.auth-user.${key}`, value);
        })""" + f"({json.dumps(values)})")
    page = await context.new_page()
    page.set_default_timeout(7000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    async def cognito(route):
        operation = route.request.headers.get("x-amz-target", "").split(".")[-1]
        body = route.request.post_data_json
        state["calls"].append(operation)
        status, result = 200, {}

        def failure(code):
            return 400, {"__type": code, "message": "AWS private detail"}

        if operation == "GetUser":
            result = {"Username": "auth-user", "UserAttributes": [
                {"Name": "email", "Value": EMAIL},
                {"Name": "email_verified", "Value": str(state["verified"]).lower()},
            ]}
        elif operation == "ChangePassword":
            claims = json.loads(base64.urlsafe_b64decode(body["AccessToken"].split(".")[1] + "=="))
            assert claims["sub"] == "auth-user" and claims["token_use"] == "access"
            assert body["ProposedPassword"] == "new-password-123"
            if body["PreviousPassword"] == "wrong-password":
                status, result = failure("NotAuthorizedException")
            else:
                assert body["PreviousPassword"] == "current-password"
                state["changes"] += 1
                await asyncio.sleep(0.2)
        elif operation == "ForgotPassword":
            assert body["Username"] == EMAIL
            await asyncio.sleep(0.2)
            result = {"CodeDeliveryDetails": {
                "AttributeName": "email", "DeliveryMedium": "EMAIL", "Destination": "i***@e***",
            }}
        elif operation == "ConfirmForgotPassword":
            assert body["Username"] == EMAIL
            assert body["Password"] == "new-password-123"
            if body["ConfirmationCode"] != "123456":
                status, result = failure(
                    "ExpiredCodeException" if body["ConfirmationCode"] == "111111" else "CodeMismatchException"
                )
        elif operation == "GetUserAttributeVerificationCode":
            assert body["AttributeName"] == "email"
            result = {"CodeDeliveryDetails": {
                "AttributeName": "email", "DeliveryMedium": "EMAIL", "Destination": "i***@e***",
            }}
        elif operation == "VerifyUserAttribute":
            assert body["AttributeName"] == "email"
            if body["Code"] == "123456":
                state["verified"] = True
            else:
                status, result = failure("CodeMismatchException")
        elif operation == "InitiateAuth":
            if body["AuthFlow"] == "REFRESH_TOKEN_AUTH":
                result = {"AuthenticationResult": {
                    "IdToken": token(True), "AccessToken": token(True, True),
                    "ExpiresIn": 3600, "TokenType": "Bearer",
                }}
            else:
                result = {"ChallengeName": "PASSWORD_VERIFIER", "Session": "srp-session",
                          "ChallengeParameters": {
                              "USER_ID_FOR_SRP": "auth-user", "SRP_B": "5", "SALT": "1",
                              "SECRET_BLOCK": base64.b64encode(b"test-block").decode(),
                          }}
        elif operation == "RespondToAuthChallenge":
            if body["ChallengeName"] == "PASSWORD_VERIFIER":
                result = {"ChallengeName": "NEW_PASSWORD_REQUIRED", "Session": "new-password-session",
                          "ChallengeParameters": {
                              "userAttributes": json.dumps({"email": EMAIL}), "requiredAttributes": "[]",
                          }}
            else:
                assert body["ChallengeName"] == "NEW_PASSWORD_REQUIRED"
                assert body["ChallengeResponses"]["NEW_PASSWORD"] == "new-password-123"
                result = {"AuthenticationResult": {
                    "IdToken": token(), "AccessToken": token(True, True), "RefreshToken": "refresh-token",
                    "ExpiresIn": 3600, "TokenType": "Bearer",
                }}
        else:
            raise AssertionError(f"Unexpected Cognito operation: {operation}")
        await route.fulfill(status=status, content_type="application/x-amz-json-1.1", body=json.dumps(result))

    await page.route("https://cognito-idp.*.amazonaws.com/**", cognito)
    await page.goto(base + ("/?account" if account else "/"))

    async def passwords():
        await page.get_by_label("새 비밀번호", exact=True).fill("new-password-123")
        await page.get_by_label("새 비밀번호 확인", exact=True).fill("new-password-123")

    if account:
        await page.get_by_role("tab", name="계정", exact=True).click()
        await expect(page.get_by_text(EMAIL, exact=True)).to_be_visible()
    if name in {"change", "mobile"}:
        await page.get_by_label("현재 비밀번호", exact=True).fill("wrong-password")
        await passwords()
        await page.get_by_label("새 비밀번호 확인", exact=True).fill("does-not-match")
        await page.get_by_role("button", name="비밀번호 변경", exact=True).click()
        await expect(page.get_by_role("alert")).to_contain_text("일치하지")
        assert "ChangePassword" not in state["calls"]
        await page.get_by_label("새 비밀번호 확인", exact=True).fill("new-password-123")
        await page.get_by_role("button", name="비밀번호 변경", exact=True).click()
        await expect(page.get_by_role("alert")).to_contain_text("현재 비밀번호")
        await page.get_by_label("현재 비밀번호", exact=True).fill("current-password")
        await page.get_by_role("button", name="비밀번호 변경", exact=True).evaluate(
            "button => { button.form.requestSubmit(); button.form.requestSubmit(); }"
        )
        await expect(page.get_by_role("status")).to_contain_text("변경되었습니다")
        assert state["changes"] == 1
        await expect(page.get_by_label("현재 비밀번호", exact=True)).to_have_value("")
        await expect(page.get_by_label("새 비밀번호", exact=True)).to_have_value("")
        assert await page.evaluate(
            "localStorage.getItem('CognitoIdentityServiceProvider.auth-test-client.LastAuthUser')"
        ) == "auth-user"
        assert "new-password-123" not in await page.evaluate("JSON.stringify(localStorage)")
    elif name == "verification":
        assert "GetUserAttributeVerificationCode" not in state["calls"]
        await page.get_by_role("button", name="인증 코드 받기", exact=True).click()
        await page.get_by_label("이메일 인증 코드", exact=True).fill("000000")
        await page.get_by_role("button", name="이메일 인증", exact=True).click()
        await expect(page.get_by_role("alert")).to_contain_text("일치하지")
        await page.get_by_label("이메일 인증 코드", exact=True).fill("123456")
        await page.get_by_role("button", name="이메일 인증", exact=True).click()
        await expect(page.get_by_text("인증 완료", exact=True)).to_be_visible()
        assert "InitiateAuth" in state["calls"]
    elif name == "first-login":
        await page.get_by_label("이메일", exact=True).fill(EMAIL)
        await page.get_by_label("비밀번호", exact=True).fill("temporary-password")
        await page.get_by_role("button", name="로그인", exact=True).click()
        await expect(page.get_by_role("heading", name="새 비밀번호 설정")).to_be_visible()
        await passwords()
        await page.get_by_role("button", name="비밀번호 설정", exact=True).click()
        await expect(page.get_by_text("로그인 완료", exact=True)).to_be_visible()
    else:
        await page.get_by_role("button", name="비밀번호 찾기", exact=True).click()
        await page.get_by_label("이메일", exact=True).fill(EMAIL)
        await page.get_by_role("button", name="인증 코드 받기", exact=True).evaluate(
            "button => { button.form.requestSubmit(); button.form.requestSubmit(); }"
        )
        await expect(page.get_by_label("인증 코드", exact=True)).to_be_visible()
        assert state["calls"].count("ForgotPassword") == 1
        await page.get_by_label("인증 코드", exact=True).fill("111111")
        await passwords()
        await page.get_by_role("button", name="비밀번호 재설정", exact=True).click()
        await expect(page.get_by_role("alert")).to_contain_text("만료")
        await page.get_by_role("button", name="코드 다시 받기", exact=True).click()
        await expect(page.get_by_role("alert")).to_have_count(0)
        await page.get_by_label("인증 코드", exact=True).fill("123456")
        await page.get_by_role("button", name="비밀번호 재설정", exact=True).click()
        await expect(page.get_by_role("heading", name="로그인", exact=True)).to_be_visible()
        await expect(page.get_by_role("status")).to_contain_text("재설정되었습니다")
        await expect(page.get_by_label("비밀번호", exact=True)).to_have_value("")
        assert "InitiateAuth" not in state["calls"]
        assert not await page.evaluate(
            "localStorage.getItem('CognitoIdentityServiceProvider.auth-test-client.LastAuthUser')"
        )

    assert not errors, errors
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if account:
        assert await page.get_by_role("tab", name="계정", exact=True).evaluate(
            "tab => { const r = tab.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth; }"
        )
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(ARTIFACTS / f"{name}.png"), full_page=True)
    await context.close()
    print(f"PASS {name}", flush=True)


async def main():
    names = sys.argv[1:] or ["change", "recovery", "verification", "first-login", "mobile"]
    with tempfile.TemporaryDirectory(prefix="transmeet-auth-fixture-") as folder:
        directory = Path(folder)
        build_fixture(directory)
        server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=folder))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    for name in names:
                        await scenario(browser, f"http://127.0.0.1:{server.server_port}", name)
                finally:
                    await browser.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    asyncio.run(main())
