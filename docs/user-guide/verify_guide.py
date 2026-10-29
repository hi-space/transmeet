"""Check the delivered HTML in a browser, offline, on desktop/mobile and in print.

Run: python3 docs/user-guide/build_guide.py
     python3 docs/user-guide/verify_guide.py

Screenshots and a print PDF are written to /tmp/transmeet-guide-qa.
Does not run or contact the production app.
"""

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "public/guide/index.html"
ARTIFACTS = Path("/tmp/transmeet-guide-qa")


async def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    errors = []
    requests = []
    results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000},
            locale="ko-KR", reduced_motion="reduce",
        )
        await context.set_offline(True)
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url)
                if request.url.startswith(("http:", "https:")) else None)
        await page.goto(GUIDE.as_uri())
        await page.evaluate("document.fonts.ready")
        await page.evaluate("""() => Promise.all(
          [...document.querySelectorAll('.screenshot img')].map(image => image.decode())
        )""")
        await expect(page.locator(".chapter")).to_have_count(19)
        await expect(page.locator(".screenshot img")).to_have_count(34)
        assert await page.evaluate("""() => [...document.querySelectorAll('img')]
          .every(image => image.complete && image.naturalWidth > 0)""")
        assert not requests, requests
        results.append("19 chapters and 34 images load without network access")

        for width in (1440, 1024, 768, 390, 320):
            await page.set_viewport_size({"width": width, "height": 1000 if width > 800 else 844})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            for ident in ("screen-map", "settings", "templates", "account", "mobile"):
                await page.locator("#" + ident).evaluate(
                    "element => element.scrollIntoView({block:'start',behavior:'instant'})")
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, ident)
            results.append(f"No horizontal page overflow at {width}px")

        await page.set_viewport_size({"width": 1440, "height": 1000})
        await page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await page.screenshot(path=str(ARTIFACTS / "desktop.png"))
        for ident in ("screen-map", "settings", "templates", "account"):
            await page.locator("#" + ident).evaluate(
                "element => element.scrollIntoView({block:'start',behavior:'instant'})")
            await page.screenshot(path=str(ARTIFACTS / f"{ident}.png"))

        await page.keyboard.press("/")
        search = page.get_by_role("searchbox", name="가이드 검색")
        await expect(search).to_be_focused()
        for term, expected in (
            ("무음", "인식 방식과 자막 흐름 조절하기"),
            ("비밀번호", "이메일 인증과 비밀번호 관리"),
            ("이메일 인증", "이메일 인증과 비밀번호 관리"),
            ("양식", "팀에서 쓰는 요약 양식 만들기"),
        ):
            await search.fill(term)
            await expect(page.locator("#search-list")).to_contain_text(expected)
        await search.fill("가이드에없는검색어zz999")
        await expect(page.get_by_role("status").filter(has_text="검색 결과가 없습니다")).to_be_visible()
        await page.keyboard.press("Escape")
        await expect(page.locator("#contents")).to_be_visible()

        # A match inside collapsed troubleshooting must actually reveal its content.
        await search.fill("버튼으로 처리 상태")
        finish_result = page.locator('#search-list a[href="#finish"]')
        await expect(finish_result).to_be_visible()
        await finish_result.click()
        await expect(page.locator(".search-hit")).to_be_focused()
        await search.fill("영어 음성이 안 들리")
        await page.locator("#search-list a").first.click()
        detail = page.locator("details").filter(has=page.get_by_text(
            "영어 음성이 안 들리거나 갑자기 재생돼요.", exact=True))
        assert await detail.get_attribute("open") is not None
        await search.fill("")
        results.append("Search, no-results, keyboard shortcut and collapsed answers work")

        await page.locator("#screen-workspace .screen-open").click()
        await expect(page.get_by_role("dialog")).to_be_visible()
        await expect(page.get_by_role("button", name="확대 이미지 닫기")).to_be_focused()
        assert await page.locator("#viewer-image img").evaluate("(image) => image.naturalWidth > 0")
        assert await page.locator("#viewer-image .annotation").count() == 6
        await page.keyboard.press("Escape")
        await expect(page.get_by_role("dialog")).not_to_be_visible()
        await expect(page.locator("#screen-workspace .screen-open")).to_be_focused()
        await page.locator("#show-annotations").uncheck()
        await expect(page.locator("#screen-workspace .annotation").first).not_to_be_visible()
        await page.locator("#show-annotations").check()
        results.append("Image viewer, annotations, Escape and focus restoration work")

        await page.locator("#copy-template").click()
        await expect(page.locator("#copy-status")).to_contain_text("복사했습니다")
        results.append("Template example copies from a local HTML file")

        await page.set_viewport_size({"width": 390, "height": 844})
        await page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await page.screenshot(path=str(ARTIFACTS / "mobile.png"))
        print_button = page.get_by_role("button", name="가이드 인쇄 또는 PDF로 저장")
        await expect(print_button).to_be_visible()
        menu = page.get_by_role("button", name="목차", exact=True)
        await menu.focus()
        await page.keyboard.press("Tab")
        assert not await page.evaluate("document.getElementById('guide-sidebar').contains(document.activeElement)")
        await menu.click()
        await expect(search).to_be_focused()
        await page.screenshot(path=str(ARTIFACTS / "mobile-menu.png"))
        await page.keyboard.press("Escape")
        await expect(menu).to_be_focused()
        await expect(page.locator("#guide-sidebar")).not_to_be_visible()
        await menu.click()
        await page.locator('#contents a[href="#account"]').click()
        await expect(page.locator("#guide-sidebar")).not_to_be_visible()
        assert "no-scroll" not in (await page.locator("body").get_attribute("class") or "")
        results.append("Mobile navigation hides offscreen focus targets and exposes named controls")

        await page.locator("#screen-account-settings .screen-open").click()
        await expect(page.get_by_role("dialog")).to_be_visible()
        await page.screenshot(path=str(ARTIFACTS / "mobile-viewer.png"))
        await page.get_by_role("button", name="확대 이미지 닫기").click()

        # Print must include all initially collapsed screenshots and instructions.
        await page.set_viewport_size({"width": 1440, "height": 1000})
        original_details = await page.locator("details").evaluate_all("(items) => items.map(item => item.open)")
        await page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
        assert await page.locator("details").evaluate_all("(items) => items.every(item => item.open)")
        await page.emulate_media(media="print")
        assert await page.locator(".screenshot").evaluate_all(
            "(items) => items.every(item => item.getBoundingClientRect().height > 0)")
        await page.pdf(path=str(ARTIFACTS / "print-check.pdf"), print_background=True,
                       prefer_css_page_size=True)
        await page.evaluate("window.dispatchEvent(new Event('afterprint'))")
        assert await page.locator("details").evaluate_all("(items) => items.map(item => item.open)") == original_details
        await page.emulate_media(media="screen")
        results.append("Print expands all details and restores their previous state")

        assert not errors, errors
        assert not requests, requests
        await context.close()

        # Progressive enhancement: the document is still readable without JS.
        plain_context = await browser.new_context(java_script_enabled=False)
        plain_page = await plain_context.new_page()
        await plain_page.goto(GUIDE.as_uri())
        await expect(plain_page.locator(".chapter")).to_have_count(19)
        await expect(plain_page.locator("noscript .callout")).to_be_visible()
        await plain_context.close()
        await browser.close()
        results.append("Document remains readable without JavaScript")

    report = {"passed": True, "checks": results, "browser_errors": errors,
              "external_requests": requests, "artifacts": str(ARTIFACTS)}
    (ARTIFACTS / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
