"""Check independent speech controls against the app, with controlled AWS boundaries.

Start Next with NEXT_PUBLIC_API_ENDPOINT=<base>/__ui_api,
NEXT_PUBLIC_WS_ENDPOINT=ws://<host>/__ui_ws, and empty Cognito public settings.
Run: TRANSMEET_TEST_URL=http://127.0.0.1:3194 python tests/speech_languages_browser.py
"""
import asyncio
import json
import os
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

BASE = os.environ.get("TRANSMEET_TEST_URL", "http://127.0.0.1:3194").rstrip("/")

SOCKET = """
window.__speechRequests = [];
window.__speechSockets = [];
window.__finishSpeechTranslations = true;
const NativeWebSocket = window.WebSocket;
class SpeechSocket {
  static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
  readyState = 0;
  constructor(url, protocols) {
    if (!url.includes('/__ui_ws')) return new NativeWebSocket(url, protocols);
    window.__speechSockets.push(this);
    setTimeout(() => { this.readyState = 1; this.onopen?.({}); }, 0);
  }
  send(raw) {
    const data = JSON.parse(raw);
    window.__speechRequests.push(data);
    if (data.action === 'prepareStt' || data.action === 'sttStatus') {
      setTimeout(() => this.emit({type: 'stt_status', provider: data.sttProvider,
        status: 'ready', managed: true, instances: 1}), 0);
    }
    if (data.action === 'translateMessage' && window.__finishSpeechTranslations) {
      setTimeout(() => this.emit({type: 'subtitle_stream', phase: 'done',
        messageId: data.messageId, speaker: data.speaker, originalText: data.originalText,
        translatedText: data.sourceLang === data.targetLang ? data.originalText : 'Translated.',
        detectedLanguage: data.sourceLang, targetLanguage: data.targetLang,
        timestamp: new Date().toISOString()}), 0);
    }
  }
  emit(data) { this.onmessage?.({data: JSON.stringify(data)}); }
  close() { this.readyState = 3; this.onclose?.({code: 1000}); }
}
window.WebSocket = SpeechSocket;
"""


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=[
            "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
        ])
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            permissions=["microphone"], service_workers="block",
        )
        await context.add_init_script("delete Navigator.prototype.serviceWorker")
        await context.add_init_script(SOCKET)
        await context.add_init_script("""
          if (!localStorage.getItem('speech-test-initialized')) {
            localStorage.setItem('speech-test-initialized', '1');
            localStorage.setItem('transmeet-settings', JSON.stringify({
              sourceLang: 'auto', targetLang: 'ko', sttProvider: 'transcribe',
              autoSummarizeMessageCount: 0, ttsAutoPlay: false
            }));
          }
        """)
        page = await context.new_page()
        page.set_default_timeout(10_000)
        errors = []
        page.on("pageerror", lambda error: errors.append(error.stack or str(error)))
        meeting = {
            "meetingId": "speech-meeting", "title": "한영 언어 설정 검증",
            "createdAt": "2026-10-28T10:00:00Z", "messageCount": 1,
            "messages": [{
                "id": "saved", "speaker": "speaker2", "originalText": "저장된 회의 발언입니다.",
                "translatedText": "Saved speech.", "detectedLanguage": "ko", "targetLanguage": "en",
                "timestamp": "2026-10-28T10:00:00Z",
            }],
        }

        async def route_api(route):
            request = route.request
            path = urlparse(request.url).path.removeprefix("/__ui_api")
            if request.method == "GET" and path == "/meetings":
                result = [{k: meeting[k] for k in ("meetingId", "title", "createdAt", "messageCount")}]
            elif request.method == "GET" and path == "/me/summary-templates":
                result = {"templates": []}
            elif request.method == "GET" and path == "/meetings/speech-meeting":
                result = meeting
            elif request.method == "POST" and path == "/meetings":
                result = {
                    "meetingId": "empty", "title": "새 회의",
                    "createdAt": "2026-10-28T11:00:00Z", "messages": [], "messageCount": 0,
                }
            elif request.method == "DELETE" and path == "/meetings/empty":
                await route.fulfill(status=204)
                return
            else:
                raise AssertionError(f"Unexpected API: {request.method} {path}")
            await route.fulfill(content_type="application/json", body=json.dumps(result))

        await page.route("**/__ui_api/**", route_api)

        async def select_provider(label):
            await page.get_by_role("button", name="설정", exact=True).click()
            await page.get_by_role("tab", name="음성 · 번역", exact=True).click()
            await page.get_by_text("고급 설정", exact=True).click()
            await page.get_by_role("button", name=label, exact=True).click()
            await page.keyboard.press("Escape")

        async def start_requests():
            return await page.evaluate(
                "window.__speechRequests.filter(r => r.action === 'startRecording')"
            )

        try:
            await page.goto(BASE + "/?meeting=speech-meeting", wait_until="domcontentloaded")
            source = page.get_by_role("combobox", name="음성 언어", exact=True)
            target = page.get_by_role("combobox", name="번역 대상", exact=True)
            await expect(source).to_have_value("auto")
            await expect(target).to_have_value("auto")

            await source.select_option("ko")
            await expect(target).to_have_value("auto")
            await target.select_option("ko")
            await expect(source).to_have_value("ko")
            await page.reload(wait_until="domcontentloaded")
            await expect(source).to_have_value("ko")
            await expect(target).to_have_value("ko")

            await select_provider("Qwen3-ASR")
            await expect(source).to_be_disabled()
            await expect(source).to_have_value("auto")
            await expect(target).to_be_enabled()
            await target.select_option("en")
            assert await page.evaluate(
                "JSON.parse(localStorage.getItem('transmeet-settings')).sourceLang"
            ) == "ko"
            await select_provider("Whisper")
            await expect(source).to_be_enabled()
            await expect(source).to_have_value("ko")
            await expect(target).to_have_value("en")
            print("PASS: migration, independent choices, persistence, Qwen input and preference restoration")

            await source.select_option("auto")
            await target.select_option("auto")
            await page.get_by_role("button", name="녹음 시작", exact=True).click()
            await page.wait_for_function(
                "window.__speechRequests.some(r => r.action === 'startRecording')"
            )
            first = (await start_requests())[-1]
            assert first["sourceLang"] == "auto" and first["translationTarget"] == "auto"
            # Collect real browser microphone frames, then change the target before
            # Whisper's six-second chunk interval has elapsed.
            await page.wait_for_timeout(450)
            await target.select_option("ko")
            await page.wait_for_function(
                "window.__speechRequests.filter(r => r.action === 'startRecording').length >= 2"
            )
            requests = await page.evaluate("window.__speechRequests")
            starts = [index for index, request in enumerate(requests) if request["action"] == "startRecording"]
            assert requests[starts[-1]]["translationTarget"] == "ko"
            assert any(r["action"] == "sendAudio" and r.get("audioData")
                       for r in requests[starts[-2] + 1:starts[-1]])
            await page.get_by_role("button", name="녹음 중지", exact=True).click()
            print("PASS: changing target during recording flushes audio before restarting")

            await target.select_option("auto")
            await select_provider("Transcribe")
            await page.get_by_role("button", name="녹음 시작", exact=True).click()
            for language, destination, text in [
                ("ko", "en", "첫 번째 부분 발언을 번역합니다."),
                ("en", "ko", "The next partial sentence is English."),
                ("ko", "en", "다음 부분 발언은 한국어입니다."),
            ]:
                await page.evaluate("""data => window.__speechSockets.findLast(s => s.readyState === 1).emit({
                  type: 'subtitle_stream', phase: 'stt_partial', messageId: 'partial',
                  speaker: 'speaker1', originalText: data.text, detectedLanguage: data.language,
                  timestamp: new Date().toISOString()
                })""", {"language": language, "text": text})
                await page.wait_for_function(
                    "text => window.__speechRequests.some(r => r.action === 'translateMessage' && r.originalText === text)",
                    arg=text,
                )
                request = await page.evaluate(
                    "text => window.__speechRequests.findLast(r => r.action === 'translateMessage' && r.originalText === text)",
                    text,
                )
                assert (request["sourceLang"], request["targetLang"]) == (language, destination)

            await target.select_option("ko")
            await page.evaluate("""() => window.__speechSockets.findLast(s => s.readyState === 1).emit({
              type: 'subtitle_stream', phase: 'stt_partial', messageId: 'partial',
              speaker: 'speaker1', originalText: '대상이 같은 원문입니다.', detectedLanguage: 'ko',
              timestamp: new Date().toISOString()
            })""")
            await page.wait_for_function("""() => window.__speechRequests.some(r =>
              r.action === 'translateMessage' && r.originalText === '대상이 같은 원문입니다.'
              && r.sourceLang === 'ko' && r.targetLang === 'ko')""")
            await page.evaluate("""() => window.__speechSockets.findLast(s => s.readyState === 1).emit({
              type: 'subtitle_stream', phase: 'done', messageId: '__pending__',
              speaker: 'speaker1', originalText: '지난 부분 발언입니다.', translatedText: 'STALE_TARGET_RESPONSE',
              detectedLanguage: 'ko', targetLanguage: 'en', timestamp: new Date().toISOString()
            })""")
            await page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            await expect(page.get_by_text("STALE_TARGET_RESPONSE", exact=True)).to_have_count(0)
            await page.get_by_role("button", name="녹음 중지", exact=True).click()
            await target.select_option("auto")
            print("PASS: partial translation follows detected speech and ignores an old target response")

            for index, (language, destination, text) in enumerate([
                ("ko", "en", "첫 번째 한국어 발언입니다."),
                ("en", "ko", "The second utterance is in English."),
                ("ko", "en", "세 번째는 다시 한국어입니다."),
            ]):
                await page.evaluate("""data => {
                  const socket = window.__speechSockets.findLast(s => s.readyState === 1);
                  const shared = {type: 'subtitle_stream', messageId: 'mixed-' + data.index,
                    speaker: 'speaker1', originalText: data.text, detectedLanguage: data.language,
                    targetLanguage: data.destination, timestamp: new Date().toISOString()};
                  socket.emit({...shared, phase: 'stt'});
                  socket.emit({...shared, phase: 'done', translatedText: '번역 결과 ' + data.index});
                }""", {"index": index, "language": language, "destination": destination, "text": text})
                await expect(page.get_by_text(text, exact=True)).to_be_visible()
                await expect(page.locator('[aria-label="현재 감지 언어"]:visible')).to_contain_text(
                    "한국어" if language == "ko" else "English"
                )
            await expect(page.get_by_text("첫 번째 한국어 발언입니다.", exact=True)).to_be_visible()
            await expect(page.get_by_text("The second utterance is in English.", exact=True)).to_be_visible()
            print("PASS: alternating speech retains separate languages and a live detection indicator")

            await target.select_option("ko")
            last_text = "세 번째는 다시 한국어입니다."
            last_card = page.get_by_role("article").filter(has_text=last_text)
            await last_card.locator('button[title="다시 번역"]').click()
            await expect(last_card.get_by_text(last_text, exact=True)).to_have_count(2)
            await target.select_option("en")
            await page.evaluate("""() => {
              const socket = window.__speechSockets.findLast(s => s.readyState === 1);
              const result = {type: 'subtitle_stream', messageId: 'after-manual',
                speaker: 'speaker1', originalText: '수동 번역 다음의 새 발언입니다.',
                detectedLanguage: 'ko', targetLanguage: 'en', timestamp: new Date().toISOString()};
              socket.emit({...result, phase: 'stt'});
              socket.emit({...result, phase: 'done', translatedText: 'New speech after manual translation.'});
            }""")
            await expect(page.get_by_text("수동 번역 다음의 새 발언입니다.", exact=True)).to_be_visible()
            await expect(last_card.get_by_text(last_text, exact=True)).to_have_count(2)
            print("PASS: manually changing a bubbles target cannot merge a different target into it")

            await target.select_option("auto")
            await page.get_by_role("button", name="녹음 시작", exact=True).click()
            await page.evaluate("""() => {
              window.__finishSpeechTranslations = false;
              const socket = window.__speechSockets.findLast(s => s.readyState === 1);
              socket.emit({type: 'subtitle_stream', phase: 'stt', messageId: 'older-final',
                speaker: 'speaker3', originalText: '먼저 인식된 한국어 발언입니다.',
                detectedLanguage: 'ko', targetLanguage: 'en', timestamp: new Date().toISOString()});
              socket.emit({type: 'subtitle_stream', phase: 'stt_partial', messageId: 'partial',
                speaker: 'speaker1', originalText: 'The current partial is in English.',
                detectedLanguage: 'en', timestamp: new Date().toISOString()});
            }""")
            await page.wait_for_function("""() => window.__speechRequests.some(r =>
              r.action === 'translateMessage' && r.originalText === 'The current partial is in English.')""")
            await page.evaluate("""() => {
              const socket = window.__speechSockets.findLast(s => s.readyState === 1);
              const request = window.__speechRequests.findLast(r =>
                r.action === 'translateMessage' && r.originalText === 'The current partial is in English.');
              socket.emit({type: 'subtitle_stream', phase: 'done', messageId: 'older-final',
                speaker: 'speaker3', originalText: '먼저 인식된 한국어 발언입니다.',
                translatedText: 'Older Korean result.', detectedLanguage: 'ko', targetLanguage: 'en',
                timestamp: new Date().toISOString()});
              socket.emit({type: 'subtitle_stream', phase: 'done', messageId: request.messageId,
                speaker: 'speaker1', originalText: request.originalText,
                translatedText: '현재 영어 부분 발언의 번역입니다.', detectedLanguage: 'en', targetLanguage: 'ko',
                timestamp: new Date().toISOString()});
              window.__finishSpeechTranslations = true;
            }""")
            await expect(page.get_by_text("현재 영어 부분 발언의 번역입니다.", exact=True)).to_be_visible()
            await expect(page.locator('[aria-label="현재 감지 언어"]:visible')).to_contain_text("English")
            await page.get_by_role("button", name="녹음 중지", exact=True).click()
            print("PASS: an older final completion cannot discard the current partial translation")

            for width in (390, 768, 1024, 1280):
                await page.set_viewport_size({"width": width, "height": 900})
                if width < 1024:
                    await expect(source).to_be_hidden()
                    await expect(target).to_be_hidden()
                    await page.get_by_role("button", name="설정", exact=True).click()
                await expect(source).to_be_visible()
                await expect(target).to_be_visible()
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                if width in (390, 1280):
                    await page.screenshot(path=f"/tmp/transmeet-speech-controls-{width}.png", full_page=True)
                if width < 1024:
                    await page.keyboard.press("Escape")
                    await expect(page.get_by_role("button", name="설정", exact=True)).to_be_focused()
            assert not errors, errors
            print("PASS: responsive controls at 390, 768, 1024 and 1280 pixels; no browser errors")
        except Exception:
            await page.screenshot(path="/tmp/transmeet-speech-controls-failure.png", full_page=True)
            print(json.dumps({"errors": errors, "body": (await page.locator("body").inner_text())[-2000:]},
                             ensure_ascii=False))
            raise
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
