"""Exercise theme presets in the real app with the guide's invented meeting data.

Run: npm run test:theme:browser
Screenshots: .playwright-mcp/theme-presets/
All account and meeting services are intercepted; no production requests are sent.
"""

import argparse
import asyncio
import functools
import importlib.util
import json
import os
import re
from pathlib import Path
import tempfile
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path(os.environ.get("THEME_TEST_ARTIFACTS", ROOT / ".playwright-mcp/theme-presets"))
spec = importlib.util.spec_from_file_location(
    "theme_guide_fixture", ROOT / "docs/user-guide/capture_screenshots.py"
)
guide = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guide)

PRESETS = [
    ("paper", "Paper Atelier", "light"),
    ("midnight", "Midnight Studio", "dark"),
    ("cobalt", "Cobalt Grid", "light"),
    ("clear", "Clear Focus", "light"),
]

INIT = r"""cfg => {
  if (window !== window.top) return;
  if (!['http:', 'https:'].includes(location.protocol)) return;
  window.__guideSummary = cfg.summary;
  if (!localStorage.getItem('theme-test-initialized')) {
    localStorage.setItem('theme-test-initialized', '1');
    localStorage.setItem('transmeet-settings', JSON.stringify({
      ttsAutoPlay:false, autoSummarizeMessageCount:10, sourceLang:'en',
      translationTarget:'ko', voiceFontSize:'compact'
    }));
    localStorage.setItem('transmeet-theme', JSON.stringify(cfg.theme));
    localStorage.setItem('transmeet-summary-width', '420');
    const prefix = `CognitoIdentityServiceProvider.${cfg.client}`;
    localStorage.setItem(`${prefix}.LastAuthUser`, 'guide-user');
    for (const [key,value] of Object.entries({
      idToken:cfg.id, accessToken:cfg.access, refreshToken:'documentation-preview-only', clockDrift:'0'
    })) localStorage.setItem(`${prefix}.guide-user.${key}`, value);
  }
  if (cfg.denied) {
    const read = Storage.prototype.getItem;
    const write = Storage.prototype.setItem;
    Storage.prototype.getItem = function(key) {
      if (key === 'transmeet-theme') throw new DOMException('Storage unavailable', 'SecurityError');
      return read.call(this, key);
    };
    Storage.prototype.setItem = function(key, value) {
      if (key === 'transmeet-theme') throw new DOMException('Storage unavailable', 'SecurityError');
      return write.call(this, key, value);
    };
  }
}"""

# Evaluate actual painted foreground/background pairs, including transparent ancestors.
READABILITY = r"""selectors => {
  const parse = value => {
    const parts = value.match(/[\d.]+/g)?.map(Number) || [0,0,0,0];
    return [parts[0],parts[1],parts[2],parts.length>3?parts[3]:1];
  };
  const blend = (color, bg) => color.slice(0,3).map((v,i)=>v*color[3]+bg[i]*(1-color[3]));
  const luminance = color => {
    const c = color.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
    return c[0]*.2126+c[1]*.7152+c[2]*.0722;
  };
  return selectors.map(option => {
    const {selector, property='color', backgroundSelector=null} =
      typeof option === 'string' ? {selector:option} : option;
    const el = document.querySelector(selector);
    if (!el) return {selector, missing:true};
    const chain = [];
    const backgroundElement = backgroundSelector ? document.querySelector(backgroundSelector) : el;
    for (let node=backgroundElement; node; node=node.parentElement) chain.unshift(node);
    let bg = [255,255,255];
    for (const node of chain) bg = blend(parse(getComputedStyle(node).backgroundColor), bg);
    const style = getComputedStyle(el);
    const ink = blend(parse(style[property]), bg);
    const a=luminance(ink), b=luminance(bg);
    return {selector, ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05),
      color:style[property], background:bg, fontSize:style.fontSize};
  });
}"""


async def new_context(browser, base, errors, *, width=1440, height=960, denied=False, theme=None):
    context = await browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=1.5,
        locale="ko-KR",
        timezone_id="Asia/Seoul",
        color_scheme="light",
        reduced_motion="reduce",
        is_mobile=width < 1024,
        has_touch=width < 1024,
    )
    config = {
        "client": guide.CLIENT,
        "id": guide.token(),
        "access": guide.token(access=True),
        "summary": guide.SUMMARY,
        "denied": denied,
        "theme": theme or {
            "preset": "default", "mode": "light", "accent": "cyan",
            "font": "default", "scale": 100, "lineSpacing": "normal",
        },
    }
    await context.add_init_script(
        guide.SOCKET + "\n(" + INIT + ")(" + json.dumps(config, ensure_ascii=False) + ");"
    )
    records = guide.records()

    async def route_request(route):
        url = urlparse(route.request.url)
        method = route.request.method
        if url.path.startswith("/__guide_api"):
            path = url.path.removeprefix("/__guide_api")
            if path == "/meetings" and method == "GET":
                result = list(records.values())
            elif path == "/meetings" and method == "POST":
                result = {
                    "meetingId": "guide-new", "title": "새 회의",
                    "createdAt": f"{guide.DAY}T09:30:00Z", "messages": [], "messageCount": 0,
                }
                records["guide-new"] = result
            elif path.startswith("/meetings/") and method == "GET":
                result = records[path.split("/")[2]]
            elif path == "/me/summary-templates" and method == "GET":
                result = {"templates": []}
            elif path == "/me/email" and method == "GET":
                result = {"email": "guide@example.com", "status": "verified"}
            else:
                errors.append(f"Unhandled fixture API: {method} {path}")
                result = {}
            await route.fulfill(content_type="application/json", body=json.dumps(result))
        elif url.hostname and url.hostname.startswith("cognito-idp."):
            operation = route.request.headers.get("x-amz-target", "").split(".")[-1]
            if operation == "GetUser":
                result = {"Username": "guide-user", "UserAttributes": [
                    {"Name": "email", "Value": "guide@example.com"},
                    {"Name": "email_verified", "Value": "true"},
                ]}
            elif operation == "InitiateAuth":
                result = {"AuthenticationResult": {
                    "IdToken": guide.token(), "AccessToken": guide.token(access=True),
                    "ExpiresIn": 3600, "TokenType": "Bearer",
                }}
            else:
                errors.append(f"Unexpected auth operation: {operation}")
                result = {}
            await route.fulfill(content_type="application/x-amz-json-1.1", body=json.dumps(result))
        elif url.path.endswith("audio-timer.worker.ts"):
            await route.fulfill(content_type="text/javascript", body="onmessage=()=>{};")
        elif url.hostname in {"127.0.0.1", "localhost"}:
            await route.continue_()
        else:
            errors.append(f"Blocked external request: {url.hostname}{url.path}")
            await route.abort()

    await context.route("**/*", route_request)
    context.on("page", lambda page: page.on("pageerror", lambda error: errors.append(str(error))))
    page = await context.new_page()
    page.set_default_timeout(12000)
    await page.goto(base + "/?meeting=guide-meeting", wait_until="networkidle")
    try:
        await expect(page.get_by_role("button", name="녹음 시작", exact=True)).to_be_visible()
    except AssertionError:
        await screenshot(page, "startup-failure")
        print("Startup diagnostics:", await page.locator("body").inner_text(), errors, flush=True)
        raise
    await expect(page.locator(".voice-message")).to_have_count(4)
    return context, page


async def open_settings(page):
    if await page.get_by_role("dialog", name="설정", exact=True).count() == 0:
        await page.get_by_role("button", name="설정", exact=True).click()
        quick = page.get_by_role("button", name="전체 설정", exact=True)
        if await quick.count():
            await quick.click()
    dialog = page.get_by_role("dialog", name="설정", exact=True)
    await dialog.get_by_role("tab", name="테마", exact=True).click()
    return dialog


async def close_settings(page):
    await page.get_by_role("button", name="설정 닫기", exact=True).click()


async def choose(page, preset, label):
    dialog = await open_settings(page)
    button = dialog.get_by_role("button", name=f"{label} 테마", exact=True)
    await button.click()
    await expect(button).to_have_attribute("aria-pressed", "true")
    await expect(page.locator("html")).to_have_attribute("data-preset", preset)
    return dialog


async def screenshot(page, name):
    await page.evaluate("document.fonts.ready")
    await page.screenshot(path=str(ARTIFACTS / f"{name}.png"), animations="disabled")


async def check_layout(page, name):
    issues = await page.evaluate("""() => {
      const issues=[];
      if (document.documentElement.scrollWidth > innerWidth+1) issues.push('document overflow');
      for (const selector of ['dialog[open]', '#settings-content', '.theme-preset-option', '.voice-message']) {
        for (const el of document.querySelectorAll(selector)) {
          if (el.getBoundingClientRect().width && el.scrollWidth>el.clientWidth+2)
            issues.push(selector+' overflows horizontally');
        }
      }
      for (const tab of document.querySelectorAll('.ui-tab')) {
        const label=tab.querySelector(':scope > span');
        if (!label || !tab.getBoundingClientRect().width) continue;
        const buttonBounds=tab.getBoundingClientRect(), labelBounds=label.getBoundingClientRect();
        if (labelBounds.left<buttonBounds.left-1 || labelBounds.right>buttonBounds.right+1 ||
            label.scrollWidth>label.clientWidth+2) issues.push('tab label overlaps its neighbor');
      }
      const record=document.querySelector('[aria-label="녹음 시작"]');
      if (record) {
        const b=record.getBoundingClientRect();
        if (b.width && (b.left<0 || b.right>innerWidth+1 || b.bottom>innerHeight+1))
          issues.push('recording control outside viewport');
      }
      return issues;
    }""")
    assert not issues, (name, issues)


async def check_readability(page, name, report, *, menus=False):
    selectors = [
        ".voice-original", ".voice-translation", ".summary-prose",
        ".meeting-sidebar .sidebar-new-meeting",
        '.meeting-sidebar input[aria-label="회의 검색"]',
        '.meeting-list-item[data-active="true"] [aria-current="page"] span[title]',
        '[aria-label="녹음 시작"]',
    ]
    if menus:
        selectors += [".sidebar-menu .ui-button", ".sidebar-menu .sidebar-danger"]
    values = await page.evaluate(READABILITY, selectors)
    report[name] = values
    for value in values:
        assert not value.get("missing"), (name, value)
        assert value["ratio"] >= 4.5, (name, value)


async def desktop(browser, base, errors, report):
    context, page = await new_context(browser, base, errors)
    try:
        for preset, label, preferred_mode in PRESETS:
            dialog = await choose(page, preset, label)
            assert await dialog.locator(".theme-preset-option").count() == 4
            assert await dialog.get_by_role("button", name="Soft Sage 테마", exact=True).count() == 0
            await expect(page.locator("html")).to_have_attribute("data-theme-mode", preferred_mode)
            await close_settings(page)
            await check_layout(page, preset)
            await check_readability(page, preset, report)
            await screenshot(page, f"{preset}-desktop")
            if preset == "paper":
                font = await page.locator(".voice-original").first.evaluate(
                    "el=>getComputedStyle(el).fontFamily"
                )
                assert "Noto_Serif_KR" in font, font
            if preset == "clear":
                size = await page.locator(".voice-translation").first.evaluate(
                    "el=>getComputedStyle(el).fontSize"
                )
                assert size == "17px", size
            await page.locator('.meeting-list-item[data-active="true"] button[aria-expanded]').click()
            await expect(page.locator(".sidebar-menu")).to_be_visible()
            await check_readability(page, preset + "-menu", report, menus=True)
            await page.keyboard.press("Escape")

            other_mode = "light" if preferred_mode == "dark" else "dark"
            dialog = await open_settings(page)
            await dialog.get_by_role("button", name="라이트" if other_mode == "light" else "다크", exact=True).click()
            await close_settings(page)
            await expect(page.locator("html")).to_have_attribute("data-theme-mode", other_mode)
            await check_readability(page, preset + "-" + other_mode, report)
            await screenshot(page, f"{preset}-{other_mode}")
            print(f"PASS {label}: selection, both modes, readable sidebar/menu", flush=True)

        dialog = await open_settings(page)
        await dialog.get_by_role("button", name="시스템", exact=True).click()
        await page.emulate_media(color_scheme="dark")
        await expect(page.locator("html")).to_have_class(re.compile(r"\bdark\b"))
        await page.emulate_media(color_scheme="light")
        await expect(page.locator("html")).not_to_have_class(re.compile(r"\bdark\b"))

        # Use the actual keyboard path, not an injected click.
        target = dialog.get_by_role("button", name="Paper Atelier 테마", exact=True)
        await target.focus()
        await page.keyboard.press("Tab")
        focused = await page.evaluate("""() => ({
          name:document.activeElement.getAttribute('aria-label'),
          outline:getComputedStyle(document.activeElement).outlineStyle
        })""")
        assert focused["name"] == "Midnight Studio 테마", focused
        assert focused["outline"] != "none", focused
        await page.keyboard.press("Space")
        await expect(page.locator("html")).to_have_attribute("data-preset", "midnight")

        await choose(page, "paper", "Paper Atelier")
        await dialog.get_by_role("button", name="IBM Plex Sans KR", exact=True).click()
        await dialog.get_by_role("button", name="블루", exact=True).click()
        await dialog.get_by_role("button", name="110%", exact=True).click()
        await dialog.get_by_role("button", name="여유롭게", exact=True).click()
        await close_settings(page)
        font = await page.locator(".voice-original").first.evaluate("el=>getComputedStyle(el).fontFamily")
        assert "IBM_Plex" in font and "Noto_Serif" not in font, font
        await page.reload(wait_until="networkidle")
        await expect(page.locator("html")).to_have_attribute("data-preset", "paper")
        await expect(page.locator("html")).to_have_attribute("data-font", "plex")
        await expect(page.locator("html")).to_have_attribute("data-accent", "blue")
        await expect(page.locator("html")).to_have_attribute("data-scale", "110")
        await expect(page.locator("html")).to_have_attribute("data-line-spacing", "relaxed")

        # Reload can open a fresh meeting; select the saved sample for reading-size checks.
        await page.locator(f'.meeting-sidebar span[title="{guide.TITLE}"]').click()
        await expect(page.locator(".voice-message")).to_have_count(4)
        await choose(page, "clear", "Clear Focus")
        await expect(page.locator("html")).to_have_attribute("data-scale", "110")
        await expect(page.locator("html")).to_have_attribute("data-line-spacing", "relaxed")
        await close_settings(page)
        await page.get_by_role("combobox", name="대화 글자 크기", exact=True).select_option("lg")
        larger = await page.locator(".voice-translation").first.evaluate(
            "el=>parseFloat(getComputedStyle(el).fontSize)"
        )
        assert larger > 30, larger
        await check_layout(page, "large reading")
        await open_settings(page)
        await page.get_by_role("button", name="테마 초기화", exact=True).click()
        await expect(page.locator("html")).to_have_attribute("data-preset", "default")
        await expect(page.locator("html")).to_have_attribute("data-scale", "100")
        await close_settings(page)

        # Real storage events between pages in one browser profile.
        other = await context.new_page()
        await other.goto(base + "/?meeting=guide-meeting", wait_until="networkidle")
        await choose(page, "cobalt", "Cobalt Grid")
        await expect(other.locator("html")).to_have_attribute("data-preset", "cobalt")
        await other.evaluate("localStorage.removeItem('transmeet-theme')")
        await expect(page.locator("html")).to_have_attribute("data-preset", "default")
        await other.close()
        print("PASS keyboard, font/accent overrides, reload, reading sizes, reset, cross-tab storage", flush=True)
    finally:
        await context.close()


async def mobile(browser, base, errors):
    for width in [375, 320]:
        context, page = await new_context(browser, base, errors, width=width, height=812)
        try:
            for preset, label, _mode in PRESETS:
                dialog = await choose(page, preset, label)
                await dialog.get_by_role("button", name="120%", exact=True).click()
                await check_layout(page, f"{preset}-{width}-settings")
                await page.locator("#settings-content").evaluate("el => el.scrollTop = 0")
                await screenshot(page, f"{preset}-{width}-settings")
                await close_settings(page)
                await check_layout(page, f"{preset}-{width}")
                if width == 375:
                    await screenshot(page, f"{preset}-mobile")
            print(f"PASS {width}px: all four presets, 120% scale, settings and recording controls", flush=True)
        finally:
            await context.close()


async def unavailable_storage(browser, base, errors):
    context, page = await new_context(browser, base, errors, denied=True)
    try:
        dialog = await choose(page, "clear", "Clear Focus")
        await expect(dialog.get_by_role("status")).to_contain_text("이번 방문에만")
        await close_settings(page)
        await expect(page.locator("html")).to_have_attribute("data-preset", "clear")
        await page.reload(wait_until="networkidle")
        await expect(page.locator("html")).to_have_attribute("data-preset", "default")
        print("PASS unavailable storage: changes work for the visit and fail safely on reload", flush=True)
    finally:
        await context.close()


async def sidebar_states(browser, base, errors, report):
    """Protect the actual editable and keyboard states in colored sidebars."""
    context, page = await new_context(browser, base, errors)
    failures = []

    async def measure(name, options, minimum=4.5):
        results = await page.evaluate(READABILITY, options)
        report[name] = results
        for result in results:
            if result.get("missing") or result["ratio"] < minimum:
                failures.append((name, result))

    try:
        for preset, label in [("cobalt", "Cobalt Grid"), ("clear", "Clear Focus")]:
            await choose(page, preset, label)
            await close_settings(page)
            more = page.locator('.meeting-list-item[data-active="true"] button[aria-expanded]')
            await more.hover()
            await more.evaluate("el => Promise.all(el.getAnimations().map(animation => animation.finished))")
            assert await more.evaluate("el => el.matches(':hover')")
            await measure(preset + "-active-hover", [
                '.meeting-list-item[data-active="true"] button[aria-expanded]',
            ])

        for accent in ["블루", "로즈", "청록"]:
            dialog = await choose(page, "clear", "Clear Focus")
            await dialog.get_by_role("button", name=accent, exact=True).click()
            await close_settings(page)
            await page.locator('.meeting-list-item[data-active="true"] button[aria-expanded]').click()
            await page.locator(".sidebar-menu").get_by_role("button", name="제목 편집", exact=True).click()
            editor = page.locator('.meeting-list-item[data-active="true"] input')
            await expect(editor).to_have_value(guide.TITLE)
            await measure("clear-edit-" + accent, [
                '.meeting-list-item[data-active="true"] input',
            ])
            cancel = page.locator('.meeting-list-item[data-active="true"]').get_by_role(
                "button", name="취소", exact=True
            )
            await cancel.hover()
            await cancel.evaluate("el => Promise.all(el.getAnimations().map(animation => animation.finished))")
            await measure("clear-cancel-" + accent, [
                '.meeting-list-item[data-active="true"] button[type="button"]',
            ])
            await cancel.click()

        await choose(page, "cobalt", "Cobalt Grid")
        await close_settings(page)
        await page.get_by_role("button", name="회의 목록 접기", exact=True).focus()
        for name, selector in [
            ("new", ".sidebar-new-meeting"),
            ("search", '.meeting-sidebar input[aria-label="회의 검색"]'),
        ]:
            await page.keyboard.press("Tab")
            control = page.locator(selector)
            await expect(control).to_be_focused()
            style = await control.evaluate("""el => ({
                outline:getComputedStyle(el).outlineStyle,
                width:parseFloat(getComputedStyle(el).outlineWidth)
            })""")
            if style["outline"] == "none" or style["width"] < 2:
                failures.append(("cobalt-focus-" + name, style))
            await measure("cobalt-focus-" + name, [{
                "selector": selector,
                "property": "outlineColor",
                "backgroundSelector": ".meeting-sidebar",
            }], minimum=3)
        assert not failures, failures
        print("PASS colored sidebar hover, custom-accent title editing, keyboard focus", flush=True)
    finally:
        await context.close()


async def run(base, only):
    errors, report = [], {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        try:
            if not only or only == "desktop":
                await desktop(browser, base, errors, report)
            if not only or only == "mobile":
                await mobile(browser, base, errors)
            if not only or only == "storage":
                await unavailable_storage(browser, base, errors)
            if not only or only == "sidebar":
                await sidebar_states(browser, base, errors, report)
        finally:
            await browser.close()
            (ARTIFACTS / "readability.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    assert not errors, errors
    print("Theme browser checks passed; no browser errors or external service requests.", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["desktop", "mobile", "storage", "sidebar"])
    args = parser.parse_args()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="transmeet-theme-browser-") as temporary:
        directory = Path(temporary) / "site"
        directory.mkdir()
        guide.build_preview(directory)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(guide.QuietHandler, directory=str(directory))
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            asyncio.run(run(f"http://127.0.0.1:{server.server_port}", args.only))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    main()
