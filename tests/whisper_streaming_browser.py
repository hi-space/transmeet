"""Whisper browser regression with local API/WebSocket fixtures and real capture.

Start an isolated Next dev server with NEXT_PUBLIC_API_ENDPOINT=<base>/__ui_api,
NEXT_PUBLIC_WS_ENDPOINT=ws://<host>/__ui_ws, and empty public Cognito settings.
Run: TRANSMEET_TEST_URL=http://127.0.0.1:3198 python tests/whisper_streaming_browser.py
"""
import asyncio
import base64
import io
import json
import os
import wave
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

BASE = os.environ.get("TRANSMEET_TEST_URL", "http://127.0.0.1:3198").rstrip("/")
if urlparse(BASE).hostname not in ("127.0.0.1", "localhost", "::1"):
    raise ValueError("This fixture must run against a local test server")

SOCKET = """
window.__whisperRequests = [];
window.__whisperSockets = [];
window.__pendingStatus = [];
window.__releaseStatus = () => {
  window.__holdStatus = false;
  window.__pendingStatus.splice(0).forEach(reply => reply());
};
const NativeWebSocket = window.WebSocket;
class SpeechSocket {
  static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
  readyState = 0;
  constructor(url, protocols) {
    if (!url.includes('/__ui_ws')) return new NativeWebSocket(url, protocols);
    window.__whisperSockets.push(this);
    setTimeout(() => { this.readyState = 1; this.onopen?.({}); }, 0);
  }
  send(raw) {
    const data = JSON.parse(raw);
    window.__whisperRequests.push({...data, sentAt: performance.now()});
    if (data.action === 'prepareStt' || data.action === 'sttStatus') {
      const status = {type: 'stt_status', provider: data.sttProvider,
        status: 'ready', managed: false, instances: 1};
      if (window.__streamingCapability !== null)
        status.streaming = window.__streamingCapability;
      const reply = () => this.emit(status);
      if (window.__holdStatus) window.__pendingStatus.push(reply);
      else setTimeout(reply, 0);
    }
    if (data.action === 'summarize') {
      setTimeout(() => this.emit({type: 'summary_stream', phase: 'done',
        meetingId: data.meetingId, summary: 'Local fixture summary.'}), 0);
    }
  }
  emit(data) { this.onmessage?.({data: JSON.stringify(data)}); }
  close() { this.readyState = 3; this.onclose?.({code: 1000}); }
}
window.WebSocket = SpeechSocket;
"""

# Preserve the real Web Audio graph, timer worker, WAV encoder and capture hook.
# Only selected tail tests supply samples manually to make the stop boundary exact.
AUDIO = """
window.__microphoneRequests = 0;
const nativeGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia = (...args) => {
  window.__microphoneRequests++;
  return nativeGetUserMedia(...args);
};
window.__audioProcessors = [];
const NativeAudioContext = window.AudioContext;
window.AudioContext = class extends NativeAudioContext {
  constructor() { super({sampleRate: 16000}); }
  createScriptProcessor(...args) {
    const node = super.createScriptProcessor(...args);
    let handler;
    node.addEventListener('audioprocess', event => {
      if (!window.__manualAudio) handler?.(event);
    });
    Object.defineProperty(node, 'onaudioprocess', {
      get: () => handler, set: value => { handler = value; },
    });
    window.__audioProcessors.push(node);
    return node;
  }
};
window.__pushAudio = samples => {
  window.__audioProcessors.at(-1).onaudioprocess({
    inputBuffer: {getChannelData: () => new Float32Array(samples).fill(0.25)}
  });
};
"""


def pcm(encoded):
    with wave.open(io.BytesIO(base64.b64decode(encoded)), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
        return audio.readframes(audio.getnframes())


async def requests(page, action):
    return await page.evaluate(
        "action => window.__whisperRequests.filter(r => r.action === action)", action,
    )


async def new_page(browser, *, capability=True, hold_status=False, manual_audio=False,
                   partial_mode="sentence"):
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        permissions=["microphone"], service_workers="block",
    )
    await context.add_init_script("delete Navigator.prototype.serviceWorker")
    await context.add_init_script(
        "window.__streamingCapability = " + json.dumps(capability) + ";"
        "window.__holdStatus = " + json.dumps(hold_status) + ";"
        "window.__manualAudio = " + json.dumps(manual_audio) + ";"
    )
    await context.add_init_script(SOCKET)
    await context.add_init_script(AUDIO)
    await context.add_init_script(
        "localStorage.setItem('transmeet-settings', JSON.stringify(" + json.dumps({
            "sttProvider": "whisper", "sttChunkSeconds": 2, "audioSource": "mic",
            "sourceLang": "ko", "translationTarget": "en",
            "partialTranslationMode": partial_mode, "partialThrottleMs": 500,
            "autoSummarizeMessageCount": 0, "ttsAutoPlay": False,
        }) + "))"
    )
    page = await context.new_page()
    page.set_default_timeout(10_000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    async def local_only(route):
        if urlparse(route.request.url).netloc != urlparse(BASE).netloc:
            errors.append("Unexpected external request: " + route.request.url)
            await route.abort()
        else:
            await route.continue_()

    async def api(route):
        path = urlparse(route.request.url).path.removeprefix("/__ui_api")
        record = {
            "meetingId": "whisper", "title": "Whisper browser fixture",
            "createdAt": "2026-10-28T10:00:00Z", "messageCount": 1,
            "messages": [{
                "id": "saved", "speaker": "speaker2", "originalText": "기존 발언입니다.",
                "translatedText": "Existing speech.", "detectedLanguage": "ko",
                "targetLanguage": "en", "timestamp": "2026-10-28T10:00:00Z",
            }],
        }
        if route.request.method == "GET" and path == "/meetings":
            result = [record]
        elif route.request.method == "GET" and path == "/meetings/whisper":
            result = record
        elif route.request.method == "GET" and path == "/me/summary-templates":
            result = {"templates": []}
        elif route.request.method == "POST" and path == "/meetings":
            result = {
                "meetingId": "empty", "title": "새 회의",
                "createdAt": "2026-10-28T11:00:00Z", "messages": [], "messageCount": 0,
            }
        elif route.request.method == "DELETE" and path == "/meetings/empty":
            await route.fulfill(status=204)
            return
        else:
            errors.append(f"Unexpected API: {route.request.method} {path}")
            await route.fulfill(status=500, body="Unexpected fixture request")
            return
        await route.fulfill(content_type="application/json", body=json.dumps(result))

    await context.route("**/*", local_only)
    await page.route("**/__ui_api/**", api)
    await page.goto(BASE + "/?meeting=whisper", wait_until="domcontentloaded")
    try:
        await page.wait_for_function(
            "window.__whisperRequests.some(r => r.action === 'sttStatus' && r.sttProvider === 'whisper')"
        )
    except Exception:
        print(json.dumps({
            "pageErrors": errors,
            "requests": await page.evaluate("window.__whisperRequests"),
            "body": (await page.locator("body").inner_text())[-2000:],
        }, ensure_ascii=False), flush=True)
        raise
    await expect(page.get_by_text("기존 발언입니다.", exact=True)).to_be_visible()
    if not hold_status:
        await expect(page.get_by_role("button", name="녹음 시작", exact=True)).to_be_enabled()
    return context, page, errors


async def advanced_settings(page):
    await page.get_by_role("button", name="설정", exact=True).click()
    await page.get_by_role("tab", name="음성 · 번역", exact=True).click()
    await page.get_by_text("고급 설정", exact=True).click()


async def emit(page, text, *, phase="stt_partial", utterance=1, translated=None, stream="recording:1"):
    await page.evaluate("""event => {
      window.__whisperSockets.findLast(s => s.readyState === 1).emit(event);
    }""", {
        "type": "subtitle_stream", "phase": phase, "sttProvider": "whisper",
        "streamId": stream, "utteranceId": utterance,
        "messageId": "partial" if phase == "stt_partial" else f"final-{utterance}",
        "speaker": "speaker1", "originalText": text, "translatedText": translated,
        "detectedLanguage": "ko", "targetLanguage": "en",
        "timestamp": "2026-10-28T11:00:00Z",
    })


async def capability_and_stream_frames(browser):
    context, page, errors = await new_page(browser, hold_status=True)
    try:
        waiting = page.get_by_role("button", name="엔진 상태 확인 중", exact=True)
        await expect(waiting).to_be_disabled()
        await waiting.evaluate("button => button.click()")
        assert not await requests(page, "startRecording")
        assert not await requests(page, "prepareStt")
        assert await page.evaluate("window.__microphoneRequests") == 0
        await page.evaluate("window.__releaseStatus()")
        await page.get_by_role("button", name="녹음 시작", exact=True).click()
        await page.wait_for_function(
            "window.__whisperRequests.filter(r => r.action === 'sendAudio').length >= 4"
        )
        audio = await requests(page, "sendAudio")
        start = (await requests(page, "startRecording"))[-1]
        assert start["sttProvider"] == "whisper" and start["sourceLang"] == "ko"
        assert start.get("sttStreaming") is True, "Capable Whisper recording must opt into streaming"
        assert audio[0]["sentAt"] - start["sentAt"] < 600
        assert all(0 < len(pcm(item["audioData"])) <= 8000 for item in audio)
        await emit(page, "녹음이 끝나기 전 중간 결과입니다.")
        await expect(page.get_by_text("녹음이 끝나기 전 중간 결과입니다.", exact=True)).to_be_visible()
        assert not await requests(page, "stopRecording")
        await page.get_by_role("button", name="녹음 일시정지", exact=True).click()
        assert not errors, errors
        print("PASS: first click waits without microphone/prepare; streaming sends short frames and shows an early partial", flush=True)
    finally:
        await context.close()


async def final_only_transcripts(browser, partial_mode):
    context, page, errors = await new_page(browser, partial_mode=partial_mode, manual_audio=True)
    try:
        await advanced_settings(page)
        for label in ("음성 처리 구간", "발화 중 번역"):
            await expect(page.get_by_role("group", name=label, exact=True)).to_have_count(0)
        await page.keyboard.press("Escape")
        await page.get_by_role("button", name="녹음 시작", exact=True).click()
        await emit(page, "검토 중입니다.")
        await expect(page.get_by_text("검토 중입니다.", exact=True)).to_be_visible()
        await emit(page, "검토 결과를 공유합니다.")
        await expect(page.get_by_text("검토 결과를 공유합니다.", exact=True)).to_be_visible()
        await expect(page.get_by_text("검토 중입니다.", exact=True)).to_have_count(0)
        await page.wait_for_timeout(650)
        assert not await requests(page, "translateMessage")
        await page.get_by_role("button", name="요약 생성", exact=True).click()
        await page.wait_for_function(
            "window.__whisperRequests.filter(r => r.action === 'summarize').length === 1"
        )
        first_summary = (await requests(page, "summarize"))[-1]
        assert [item["original"] for item in first_summary["messages"]] == ["기존 발언입니다."]
        await emit(page, "검토 결과를 공유합니다.", phase="stt")
        await emit(page, "검토 결과를 공유합니다.", phase="done", translated="Here are the review results.")
        await expect(page.get_by_text("Here are the review results.", exact=True)).to_be_visible()
        await expect(page.get_by_text("검토 결과를 공유합니다.", exact=True)).to_have_count(1)
        await emit(page, "새로운 발언의 중간 결과", utterance=2)
        await emit(page, "", utterance=1, stream="old-recording:1")
        await expect(page.get_by_text("새로운 발언의 중간 결과", exact=True)).to_be_visible()
        await emit(page, "", utterance=2)
        await expect(page.get_by_text("새로운 발언의 중간 결과", exact=True)).to_have_count(0)
        await page.get_by_role("button", name="녹음 일시정지", exact=True).click()
        await page.get_by_role("button", name="요약 새로 생성", exact=True).click()
        await page.wait_for_function(
            "window.__whisperRequests.filter(r => r.action === 'summarize').length === 2"
        )
        final_summary = (await requests(page, "summarize"))[-1]
        assert [item["original"] for item in final_summary["messages"]] == [
            "기존 발언입니다.", "검토 결과를 공유합니다.",
        ]
        assert not await requests(page, "translateMessage")
        assert not errors, errors
        print(f"PASS: {partial_mode} preference shows revisions, keeps only one final in meeting data, and isolates old clears", flush=True)
    finally:
        await context.close()


async def batch_compatibility(browser, capability):
    context, page, errors = await new_page(browser, capability=capability)
    try:
        await advanced_settings(page)
        window = page.get_by_role("group", name="음성 처리 구간", exact=True)
        await expect(window).to_be_visible()
        await expect(window.get_by_role("button", name="2초", exact=True)).to_have_attribute("aria-pressed", "true")
        await expect(page.get_by_role("group", name="발화 중 번역", exact=True)).to_be_visible()
        await page.keyboard.press("Escape")
        await page.get_by_role("button", name="녹음 시작", exact=True).click()
        await page.wait_for_timeout(800)
        assert not await requests(page, "sendAudio")
        await page.wait_for_function(
            "window.__whisperRequests.some(r => r.action === 'sendAudio')", timeout=4000,
        )
        first = (await requests(page, "sendAudio"))[0]
        start = (await requests(page, "startRecording"))[-1]
        assert start.get("sttStreaming") is False, "Legacy backend must retain the batch protocol"
        assert 1700 <= first["sentAt"] - start["sentAt"] < 3500
        assert 40000 <= len(pcm(first["audioData"])) <= 96000
        await page.get_by_role("button", name="녹음 일시정지", exact=True).click()
        assert not await requests(page, "prepareStt"), "Batch Whisper unexpectedly required preparation"
        assert not errors, errors
        print(f"PASS: streaming={capability} keeps batch controls and the selected two-second request window without preparation", flush=True)
    finally:
        await context.close()


async def selected_languages_and_tail(browser):
    context, page, errors = await new_page(browser, manual_audio=True)
    try:
        await page.get_by_role("button", name="녹음 시작", exact=True).click()
        await expect(page.get_by_role("button", name="녹음 일시정지", exact=True)).to_be_visible()
        for language in ("en", "auto"):
            await page.get_by_role("combobox", name="음성 언어", exact=True).evaluate(
                """(select, language) => {
                  window.__pushAudio(800);
                  select.value = language;
                  select.dispatchEvent(new Event('change', {bubbles: true}));
                }""", language,
            )
            await page.wait_for_function(
                "language => window.__whisperRequests.some(r => r.action === 'startRecording' && r.sourceLang === language)",
                arg=language,
            )
        await page.evaluate("""() => {
          const socket = window.__whisperSockets.findLast(s => s.readyState === 1);
          socket.readyState = 3;
          socket.onclose?.({code: 1006});
        }""")
        await page.wait_for_function(
            "window.__whisperRequests.filter(r => r.action === 'startRecording').length === 4"
        )
        await page.get_by_role("button", name="녹음 일시정지", exact=True).evaluate(
            "button => { window.__pushAudio(800); button.click(); }"
        )
        starts = await requests(page, "startRecording")
        audio = await requests(page, "sendAudio")
        assert [item["sourceLang"] for item in starts] == ["ko", "en", "auto", "auto"]
        assert all(item.get("sttStreaming") is True for item in starts)
        assert [item["sourceLang"] for item in audio] == ["ko", "en", "auto"]
        assert all(pcm(item["audioData"]) == b"\xff\x1f" * 800 for item in audio)
        sent = await page.evaluate("window.__whisperRequests")
        assert sent.index(audio[0]) < sent.index(starts[1])
        assert sent.index(audio[1]) < sent.index(starts[2])
        assert sent.index(audio[2]) < max(i for i, item in enumerate(sent) if item["action"] == "stopRecording")
        assert not errors, errors
        print("PASS: Korean, English, auto and reconnect starts retain opt-in; each 50 ms language/stop tail is sent once", flush=True)
    finally:
        await context.close()


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=[
            "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
        ])
        try:
            await capability_and_stream_frames(browser)
            for mode in ("sentence", "realtime"):
                await final_only_transcripts(browser, mode)
            for capability in (False, None):
                await batch_compatibility(browser, capability)
            await selected_languages_and_tail(browser)
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
