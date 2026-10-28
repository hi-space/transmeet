"""Exercise summary choices against a local dev server with mocked API/WebSocket traffic.

Run with NEXT_PUBLIC_API_ENDPOINT=<base>/__ui_api and
NEXT_PUBLIC_WS_ENDPOINT=ws://<host>/__ui_ws, then:
TRANSMEET_TEST_URL=http://127.0.0.1:3186 python tests/summary_styles_browser.py
"""
import asyncio
import json
import os
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

BASE = os.environ.get("TRANSMEET_TEST_URL", "http://127.0.0.1:3186").rstrip("/")

SOCKET = """
window.__summaryRequests = [];
window.__testSockets = [];
window.__finishSummaries = true;
const NativeWebSocket = window.WebSocket;
class TestSocket {
  static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
  readyState = 0;
  constructor(url, protocols) {
    if (!url.includes('/__ui_ws')) return new NativeWebSocket(url, protocols);
    window.__testSockets.push(this);
    setTimeout(() => { this.readyState = 1; this.onopen?.({}); }, 0);
  }
  send(raw) {
    const data = JSON.parse(raw);
    window.__summaryRequests.push(data);
    if (data.action === 'summarize' && window.__finishSummaries) {
      setTimeout(() => this.finish(data), 30);
    }
  }
  finish(data) {
    const label = data.summaryStyle === 'custom' ? {summaryLabel: '주간 회의'} : {};
    this.emit({type: 'summary_stream', phase: 'delta', meetingId: data.meetingId,
      summaryStyle: data.summaryStyle, ...label, text: '## 새 요약\\n선택한 방식의 결과'});
    this.emit({type: 'summary_stream', phase: 'done', meetingId: data.meetingId,
      summaryStyle: data.summaryStyle, ...label, summary: '## 새 요약\\n선택한 방식의 결과'});
  }
  close() { this.readyState = 3; this.onclose?.({code: 1000}); }
  emit(data) { this.onmessage?.({data: JSON.stringify(data)}); }
}
window.WebSocket = TestSocket;
"""


async def scenario(browser, name, *, auto_count=0, initial_style=None):
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900}, service_workers="block",
    )
    # Keep production PWA registration out of these mocked API tests. Merely
    # blocking registration leaves Workbox with an undefined registration.
    await context.add_init_script("delete Navigator.prototype.serviceWorker")
    await context.add_init_script(SOCKET)
    await context.add_init_script(
        "localStorage.setItem('transmeet-settings', JSON.stringify("
        + json.dumps({"autoSummarizeMessageCount": auto_count, "ttsAutoPlay": False})
        + "))"
    )
    page = await context.new_page()
    errors, rest_summaries, saves = [], [], []
    fail_save = False
    details_gate = asyncio.Event()
    if name != "loading":
        details_gate.set()
    page.on("pageerror", lambda error: errors.append(error.stack))
    records = {}
    for index in (1, 2):
        records[f"meeting-{index}"] = {
            "meetingId": f"meeting-{index}", "title": f"요약 테스트 회의 {index}",
            "createdAt": f"2026-10-28T0{index}:00:00Z", "messageCount": 2,
            "summary": f"## 기존 상세 요약 {index}\n기존 내용을 유지합니다.",
            "messages": [{
                "id": f"saved-{i}", "speaker": f"speaker{i}",
                "originalText": f"데이터 팀의 검증 일정 논의 {i}", "translatedText": "",
                "timestamp": f"2026-10-28T00:0{i}:00Z",
            } for i in (1, 2)],
        }
    if initial_style:
        records["meeting-1"]["summaryStyle"] = initial_style
    templates = []
    if name == "custom":
        templates.append({"templateId": "tpl-1", "name": "주간 회의", "kind": "instruction",
                          "body": "섹션은 이슈 / 원인 / 조치", "createdAt": "2026-10-28T00:00:00Z",
                          "updatedAt": "2026-10-28T02:00:00Z"})
        # 사본이 양식보다 오래됐다 → "최신 내용 적용" 링크
        records["meeting-1"].update(summaryStyle="custom", summaryCustom={
            "templateId": "tpl-1", "name": "주간 회의", "kind": "instruction",
            "body": "예전 본문", "templateUpdatedAt": "2026-10-28T01:00:00Z"})
        # 양식이 이미 지워진 회의
        records["meeting-2"].update(summaryStyle="custom", summaryCustom={
            "templateId": "tpl-gone", "name": "옛 양식", "kind": "instruction",
            "body": "옛 본문", "templateUpdatedAt": "2026-10-28T01:00:00Z"})

    async def route_api(route):
        request = route.request
        path = urlparse(request.url).path.removeprefix("/__ui_api")
        status = 200
        if request.method == "GET" and path == "/meetings":
            result = [{k: m[k] for k in ("meetingId", "title", "createdAt", "messageCount")} for m in records.values()]
        elif request.method == "GET" and path.startswith("/meetings/"):
            await details_gate.wait()
            result = records[path.split("/")[2]]
        elif request.method == "POST" and path == "/meetings":
            result = {"meetingId": "empty", "title": "새 회의", "createdAt": "2026-10-28T03:00:00Z",
                      "messages": [], "messageCount": 0}
        elif request.method == "GET" and path == "/me/summary-templates":
            result = {"templates": templates}
        elif request.method == "PUT" and path.endswith("/summary-style"):
            body = request.post_data_json
            saves.append(body)
            if fail_save:
                status, result = 500, {"error": "Save unavailable"}
            else:
                meeting_id = path.split("/")[2]
                records[meeting_id]["summaryStyle"] = body["summaryStyle"]
                result = {"meetingId": meeting_id, **body}
                if body["summaryStyle"] == "custom":
                    t = next(t for t in templates if t["templateId"] == body["templateId"])
                    snap = {k: t[k] for k in ("templateId", "name", "kind", "body")}
                    snap["templateUpdatedAt"] = t["updatedAt"]
                    records[meeting_id]["summaryCustom"] = snap
                    result = {"meetingId": meeting_id, "summaryStyle": "custom", "summaryCustom": snap}
        elif request.method == "POST" and path.endswith("/summarize"):
            body = request.post_data_json
            rest_summaries.append(body)
            assert body.get("mode") != "brief"
            result = {"meetingId": path.split("/")[2], "summary": "## REST 요약\n선택한 방식의 결과",
                      "summaryGeneratedStyle": body["summaryStyle"]}
        elif request.method == "DELETE":
            await route.fulfill(status=204)
            return
        else:
            raise AssertionError(f"Unexpected API: {request.method} {path}")
        await route.fulfill(status=status, content_type="application/json", body=json.dumps(result))

    await page.route("**/__ui_api/**", route_api)
    await page.goto(BASE + "/?meeting=meeting-1", wait_until="domcontentloaded")
    selector = page.get_by_role("combobox", name="요약 방식")
    generated = page.get_by_label("생성된 요약 방식", exact=True)
    if name == "loading":
        await page.wait_for_function("window.__testSockets.some(s => s.readyState === 1)")
        await page.get_by_role("button", name="녹음 작업 더보기", exact=True).click()
        try:
            await expect(page.get_by_role("button", name="회의 종료", exact=True)).to_be_disabled()
        finally:
            details_gate.set()
        await expect(selector).to_have_value("report")
        assert not await page.evaluate("window.__summaryRequests.some(r => r.action === 'endMeeting')")
        print(json.dumps({"scenario": name, "passed": True}))
        await context.close()
        return
    try:
        await expect(selector).to_have_value("custom:tpl-1" if name == "custom" else initial_style or "detailed")
    except Exception:
        print(json.dumps({"initErrors": errors, "body": await page.locator("body").inner_text()}, ensure_ascii=False))
        await page.screenshot(path="/tmp/transmeet-summary-styles-init-failure.png", full_page=True)
        raise
    await expect(generated).to_contain_text("자세하게")
    await page.wait_for_function("window.__testSockets.some(s => s.readyState === 1)")

    async def summary_requests():
        return await page.evaluate("window.__summaryRequests.filter(r => r.action === 'summarize')")

    try:
        if name == "choices":
            assert await selector.locator(":scope > option").count() == 4
            assert not await summary_requests()
            await selector.select_option("report")
            await expect(selector).to_be_enabled()
            await expect(selector).to_have_value("report")
            await expect(generated).to_contain_text("자세하게")
            assert not await summary_requests() and not rest_summaries

            # Reopening the same meeting restores the server-side selection.
            await page.goto(BASE + "/?meeting=meeting-1", wait_until="domcontentloaded")
            await expect(selector).to_have_value("report")
            fail_save = True
            await selector.select_option("detailed")
            await expect(page.get_by_role("alert").filter(has_text="요약 방식을 저장하지 못했습니다")).to_be_visible()
            await expect(selector).to_have_value("report")
            await expect(generated).to_contain_text("자세하게")
            fail_save = False
            await page.get_by_role("button", name="요약 새로 생성", exact=True).click()
            await expect(generated).to_contain_text("공유·보고용")
            requests = await summary_requests()
            assert requests[-1]["summaryStyle"] == "report"
            assert len(requests[-1]["messages"]) == 2
            assert requests[-1]["messages"][0]["original"] == "데이터 팀의 검증 일정 논의 1"

            # A late event for another meeting must not change the current content.
            await page.get_by_role("navigation", name="회의 기록").get_by_text("요약 테스트 회의 2", exact=True).click()
            await expect(selector).to_have_value("detailed")
            await page.evaluate("""() => window.__testSockets.findLast(s => s.readyState === 1).emit({
              type: 'summary_stream', phase: 'done', meetingId: 'meeting-1',
              summaryStyle: 'report', summary: '다른 회의의 지연 결과'
            })""")
            await expect(page.get_by_text("기존 상세 요약 2", exact=True)).to_be_visible()
            await expect(generated).to_contain_text("자세하게")
            await expect(page.get_by_text("다른 회의의 지연 결과", exact=True)).to_have_count(0)

            await page.set_viewport_size({"width": 390, "height": 844})
            await page.get_by_role("tab", name="요약", exact=True).click()
            await expect(selector).to_be_visible()
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            await page.screenshot(path="/tmp/transmeet-summary-styles-mobile.png", full_page=True)
            await page.set_viewport_size({"width": 1280, "height": 900})
            await page.screenshot(path="/tmp/transmeet-summary-styles-desktop.png", full_page=True)

        elif name == "automatic":
            await selector.select_option("report")
            await expect(selector).to_be_enabled()
            assert not await summary_requests()
            await page.evaluate("""() => {
              const socket = window.__testSockets.findLast(s => s.readyState === 1);
              for (let i = 0; i < 2; i++) socket.emit({
                type: 'subtitle', speaker: 'speaker' + (i + 1),
                originalText: '추가 검증 결과 ' + i, translatedText: '',
                timestamp: '2026-10-28T01:0' + i + ':00Z'
              });
            }""")
            await expect(generated).to_contain_text("공유·보고용")
            requests = await summary_requests()
            assert len(requests) == 1 and requests[0]["summaryStyle"] == "report"

        elif name == "end":
            await page.get_by_role("button", name="녹음 작업 더보기", exact=True).click()
            await page.get_by_role("button", name="회의 종료", exact=True).click()
            await page.wait_for_function("window.__summaryRequests.some(r => r.action === 'endMeeting')")
            request = await page.evaluate("window.__summaryRequests.find(r => r.action === 'endMeeting')")
            assert request["summaryStyle"] == "report"
            await expect(selector).to_be_disabled()
            await page.evaluate("""request => {
              const socket = window.__testSockets.findLast(s => s.readyState === 1);
              socket.finish(request);
              socket.emit({type: 'meeting_end', meetingId: request.meetingId, phase: 'done'});
            }""", request)
            await expect(generated).to_contain_text("공유·보고용")
            await expect(selector).to_be_enabled()

        elif name == "custom":
            group = selector.locator('optgroup[label="내 양식"]')
            await expect(group.locator('option[value^="custom:"]')).to_have_count(1)
            await expect(selector).to_have_value("custom:tpl-1")
            # 사본이 오래됐다 → 링크 한 번으로 최신 사본
            refresh = page.get_by_role("button", name="최신 내용 적용")
            await expect(page.get_by_text("양식이 수정됐습니다")).to_be_visible()
            await refresh.click()
            await expect(refresh).to_have_count(0)
            assert saves[-1] == {"summaryStyle": "custom", "templateId": "tpl-1"}, saves
            # 기본 방식 → 다시 내 양식
            await selector.select_option("report")
            await expect(selector).to_have_value("report")
            await selector.select_option("custom:tpl-1")
            await expect(selector).to_have_value("custom:tpl-1")
            assert saves[-1] == {"summaryStyle": "custom", "templateId": "tpl-1"}, saves
            # 생성하면 양식 이름이 표시줄에
            await page.get_by_role("button", name="요약 새로 생성", exact=True).click()
            await expect(generated).to_contain_text("주간 회의")
            requests = await summary_requests()
            assert requests[-1]["summaryStyle"] == "custom"
            assert "섹션은 이슈" not in json.dumps(requests[-1], ensure_ascii=False)
            # 관리 항목은 선택값이 아니라 설정의 요약 양식 탭을 연다
            await selector.select_option("__manage__")
            await expect(selector).to_have_value("custom:tpl-1")
            dialog = page.get_by_role("dialog")
            await expect(dialog.get_by_role("tab", name="요약 양식")).to_have_attribute("aria-selected", "true")
            await expect(dialog.get_by_text("주간 회의")).to_be_visible()
            await dialog.get_by_role("button", name="새 양식").click()
            await expect(dialog.get_by_text("메일에서는 제목·불릿·표·문단만 그대로 보입니다")).to_be_visible()
            body = dialog.get_by_role("textbox", name="양식 내용")
            await expect(dialog.get_by_text("0 / 6000")).to_be_visible()
            await dialog.get_by_role("button", name="기존 회의록 붙여넣기").click()
            await expect(dialog.get_by_text("0 / 12000")).to_be_visible()
            await dialog.get_by_role("textbox", name="양식 이름").fill("새 양식")
            await body.fill("a" * 12001)
            await expect(dialog.get_by_role("button", name="저장")).to_be_disabled()
            await body.fill("## 이슈\n- 내용")
            await expect(dialog.get_by_role("button", name="저장")).to_be_enabled()
            await dialog.get_by_role("button", name="설정 닫기").click()
            # 지워진 양식을 쓰던 회의
            await page.get_by_role("navigation", name="회의 기록").get_by_text("요약 테스트 회의 2", exact=True).click()
            await expect(selector).to_have_value("custom:tpl-gone")
            await expect(selector.locator('option[value="custom:tpl-gone"]')).to_have_text("옛 양식 (삭제된 양식)")

        elif name == "rest":
            await page.evaluate("window.__testSockets.findLast(s => s.readyState === 1).close()")
            await page.get_by_role("button", name="요약 새로 생성", exact=True).click()
            await expect(generated).to_contain_text("공유·보고용")
            assert rest_summaries == [{"summaryStyle": "report"}]
            assert not await summary_requests()

        assert not errors, errors
        print(json.dumps({"scenario": name, "passed": True, "saves": len(saves)}, ensure_ascii=False))
    except Exception:
        await page.screenshot(path=f"/tmp/transmeet-summary-styles-{name}-failure.png", full_page=True)
        print(json.dumps({"scenario": name, "errors": errors, "body": (await page.locator("body").inner_text())[-2400:]}, ensure_ascii=False))
        raise
    finally:
        await context.close()


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            only = os.environ.get("TRANSMEET_TEST_SCENARIO")
            for name, options in [
                ("choices", {}),
                ("automatic", {"auto_count": 2}),
                ("end", {"initial_style": "report"}),
                ("rest", {"initial_style": "report"}),
                ("loading", {"initial_style": "report"}),
                ("custom", {}),
            ]:
                if not only or name == only:
                    await scenario(browser, name, **options)
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
