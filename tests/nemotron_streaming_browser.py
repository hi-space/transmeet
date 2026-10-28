"""Verify Nemotron capture and subtitles against the real app.

Use the same isolated dev server settings as speech_languages_browser.py:
NEXT_PUBLIC_API_ENDPOINT=<base>/__ui_api, NEXT_PUBLIC_WS_ENDPOINT=ws://<host>/__ui_ws,
and empty public Cognito settings.
Run: TRANSMEET_TEST_URL=http://127.0.0.1:3196 python tests/nemotron_streaming_browser.py
"""
import asyncio
import base64
import io
import json
import os
import wave
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

BASE = os.environ.get("TRANSMEET_TEST_URL", "http://127.0.0.1:3196").rstrip("/")

SOCKET = """
window.__nemotronRequests = [];
window.__nemotronSockets = [];
const NativeWebSocket = window.WebSocket;
class SpeechSocket {
  static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
  readyState = 0;
  constructor(url, protocols) {
    if (!url.includes('/__ui_ws')) return new NativeWebSocket(url, protocols);
    window.__nemotronSockets.push(this);
    setTimeout(() => { this.readyState = 1; this.onopen?.({}); }, 0);
  }
  send(raw) {
    const data = JSON.parse(raw);
    window.__nemotronRequests.push({...data, sentAt: performance.now()});
    if (data.action === 'prepareStt' || data.action === 'sttStatus') {
      const status = {type: 'stt_status', provider: data.sttProvider,
        status: 'ready', managed: true, instances: 1};
      if (window.__streamingCapability !== null)
        status.streaming = window.__streamingCapability;
      setTimeout(() => this.emit(status), 0);
    }
  }
  emit(data) { this.onmessage?.({data: JSON.stringify(data)}); }
  close() { this.readyState = 3; this.onclose?.({code: 1000}); }
}
window.WebSocket = SpeechSocket;
"""

# Keep the real audio graph and hook. Only microphone sample delivery is
# controlled in tail tests so a timer cannot race with a stop/language click.
AUDIO = """
window.__audioProcessors = [];
const NativeAudioContext = window.AudioContext;
window.AudioContext = class extends NativeAudioContext {
  constructor() { super({sampleRate: window.__inputSampleRate}); }
  createScriptProcessor(...args) {
    const node = super.createScriptProcessor(...args);
    let handler;
    node.addEventListener('audioprocess', event => {
      if (!window.__manualAudio) handler?.(event);
    });
    Object.defineProperty(node, 'onaudioprocess', {
      get: () => handler,
      set: value => { handler = value; },
    });
    window.__audioProcessors.push(node);
    return node;
  }
};
window.__pushAudio = (length, value = 0.25) => {
  window.__audioProcessors.at(-1).onaudioprocess({
    inputBuffer: {getChannelData: () => new Float32Array(length).fill(value)}
  });
};
"""


def pcm(audio_data):
    with wave.open(io.BytesIO(base64.b64decode(audio_data)), "rb") as audio:
        assert audio.getframerate() == 16000
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        return audio.readframes(audio.getnframes())


async def new_page(browser, *, streaming=True, manual_audio=False, partial_mode="sentence", sample_rate=16000):
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        permissions=["microphone"], service_workers="block",
    )
    await context.add_init_script("delete Navigator.prototype.serviceWorker")
    await context.add_init_script(
        "window.__streamingCapability = " + json.dumps(streaming) + ";"
        "window.__manualAudio = " + json.dumps(manual_audio) + ";"
        "window.__inputSampleRate = " + json.dumps(sample_rate) + ";"
    )
    await context.add_init_script(SOCKET)
    await context.add_init_script(AUDIO)
    settings = {
        "sttProvider": "nemotron", "sttChunkSeconds": 10, "audioSource": "mic",
        "sourceLang": "ko", "translationTarget": "en",
        "partialTranslationMode": partial_mode, "partialThrottleMs": 500,
        "autoSummarizeMessageCount": 0, "ttsAutoPlay": False,
    }
    await context.add_init_script(
        "localStorage.setItem('transmeet-settings', JSON.stringify(" +
        json.dumps(settings) + "))"
    )
    page = await context.new_page()
    page.set_default_timeout(10_000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    async def route_api(route):
        path = urlparse(route.request.url).path.removeprefix("/__ui_api")
        record = {
            "meetingId": "nemotron", "title": "Nemotron streaming",
            "createdAt": "2026-10-28T10:00:00Z", "messageCount": 1,
            "messages": [{
                "id": "saved", "speaker": "speaker2", "originalText": "기존 발언입니다.",
                "translatedText": "Existing speech.", "detectedLanguage": "ko",
                "targetLanguage": "en", "timestamp": "2026-10-28T10:00:00Z",
            }],
        }
        if route.request.method == "GET" and path == "/meetings":
            result = [record]
        elif route.request.method == "GET" and path == "/meetings/nemotron":
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
            raise AssertionError(f"Unexpected API: {route.request.method} {path}")
        await route.fulfill(content_type="application/json", body=json.dumps(result))

    await page.route("**/__ui_api/**", route_api)
    await page.goto(BASE + "/?meeting=nemotron", wait_until="domcontentloaded")
    try:
        await page.wait_for_function(
            "window.__nemotronRequests.some(r => r.action === 'sttStatus')"
        )
    except Exception:
        print(json.dumps({
            "pageErrors": errors,
            "actions": await page.evaluate("window.__nemotronRequests?.map(r => r.action)"),
            "body": (await page.locator("body").inner_text())[-1500:],
        }, ensure_ascii=False), flush=True)
        raise
    await expect(page.get_by_role("button", name="녹음 시작", exact=True)).to_be_enabled()
    return context, page, errors


async def advanced_settings(page):
    await page.get_by_role("button", name="설정", exact=True).click()
    await page.get_by_role("tab", name="음성 · 번역", exact=True).click()
    await page.get_by_text("고급 설정", exact=True).click()


async def requests(page, action):
    return await page.evaluate(
        "action => window.__nemotronRequests.filter(r => r.action === action)", action
    )


async def settings_and_capture(browser):
    for capability, sample_rate in ((None, 16000), (False, 16000), (True, 16000), (True, 44100), (True, 48000)):
        context, page, errors = await new_page(browser, streaming=capability, sample_rate=sample_rate)
        try:
            await advanced_settings(page)
            batch_window = page.get_by_role("group", name="음성 처리 구간", exact=True)
            if capability is True:
                await expect(batch_window).to_have_count(0)
                await expect(page.get_by_role("group", name="발화 중 번역", exact=True)).to_have_count(0)
            else:
                await expect(batch_window).to_be_visible()
                await expect(batch_window.get_by_role("button", name="10초")).to_have_attribute(
                    "aria-pressed", "true"
                )
            await page.keyboard.press("Escape")
            await page.get_by_role("button", name="녹음 시작", exact=True).click()
            await page.wait_for_timeout(1100)
            sent = await requests(page, "sendAudio")
            if capability is True:
                assert len(sent) >= 4, f"Streaming waited for a batch: {len(sent)} chunks"
                start = (await requests(page, "startRecording"))[-1]
                assert sent[0]["sentAt"] - start["sentAt"] < 600
                assert all(0 < len(pcm(item["audioData"])) <= 8000 for item in sent)
            else:
                assert not sent, "A legacy backend must keep the selected batch window"
            await page.get_by_role("button", name="녹음 일시정지", exact=True).click()
            assert await requests(page, "sendAudio"), "Stopping must flush captured audio"
            assert not errors, errors
            print(f"PASS: settings and capture for streaming={capability}, input={sample_rate} Hz", flush=True)
        finally:
            await context.close()


async def partials_and_finals(browser):
    for partial_mode in ("sentence", "realtime"):
        context, page, errors = await new_page(browser, partial_mode=partial_mode)
        try:
            await page.get_by_role("button", name="녹음 시작", exact=True).click()
            async def emit(text, *, phase="stt_partial", message_id="utterance-1", translated=None):
                await page.evaluate("""data => {
                  window.__nemotronSockets.findLast(s => s.readyState === 1).emit({
                    type: 'subtitle_stream', phase: data.phase, messageId: data.id,
                    originalText: data.text, translatedText: data.translated,
                    speaker: 'speaker1', detectedLanguage: 'ko', targetLanguage: 'en',
                    timestamp: new Date().toISOString()
                  });
                }""", {"text": text, "phase": phase, "id": message_id, "translated": translated})

            await emit("검토 중입니다.")
            await expect(page.get_by_text("검토 중입니다.", exact=True)).to_be_visible()
            await emit("검토 결과를 공유합니다.")
            await expect(page.get_by_text("검토 결과를 공유합니다.", exact=True)).to_be_visible()
            await expect(page.get_by_text("검토 중입니다.", exact=True)).to_have_count(0)
            await page.wait_for_timeout(650)
            assert not await requests(page, "translateMessage"), "Speculative hypotheses were translated"
            await emit("검토 결과를 공유합니다.", phase="stt")
            await emit("검토 결과를 공유합니다.", phase="done", translated="Here are the review results.")
            await expect(page.get_by_text("Here are the review results.", exact=True)).to_be_visible()
            await expect(page.get_by_text("검토 결과를 공유합니다.", exact=True)).to_have_count(1)
            await emit("취소할 중간 결과", message_id="utterance-2")
            await expect(page.get_by_text("취소할 중간 결과", exact=True)).to_be_visible()
            await emit("", message_id="utterance-2")
            await expect(page.get_by_text("취소할 중간 결과", exact=True)).to_have_count(0)
            await page.get_by_role("button", name="녹음 일시정지", exact=True).click()
            assert not errors, errors
            print(f"PASS: full hypotheses and finalized translations with {partial_mode} preference", flush=True)
        finally:
            await context.close()


async def short_tail(browser, sample_rate=16000):
    context, page, errors = await new_page(browser, manual_audio=True, sample_rate=sample_rate)
    tail_samples = sample_rate // 20
    try:
        await page.get_by_role("button", name="녹음 시작", exact=True).click()
        # The final 50 ms must be sent once, before stopRecording, even though it
        # is shorter than the normal transport interval.
        await page.get_by_role("button", name="녹음 일시정지", exact=True).evaluate(
            "(button, samples) => { window.__pushAudio(samples); button.click(); }", tail_samples
        )
        sent = await page.evaluate("window.__nemotronRequests")
        audio = [item for item in sent if item["action"] == "sendAudio"]
        assert len(audio) == 1, f"Lost or duplicated the final 50 ms: {len(audio)} chunks"
        assert pcm(audio[0]["audioData"]) == b"\xff\x1f" * 800
        assert sent.index(audio[0]) < next(i for i, item in enumerate(sent) if item["action"] == "stopRecording")
        await page.get_by_role("button", name="녹음 재개", exact=True).click()
        await page.get_by_role("button", name="녹음 일시정지", exact=True).click()
        assert len(await requests(page, "sendAudio")) == 1, "Empty recordings must not send WAV headers"

        await page.get_by_role("button", name="녹음 재개", exact=True).click()
        await expect(page.get_by_role("button", name="녹음 일시정지", exact=True)).to_be_visible()
        await page.get_by_role("combobox", name="음성 언어", exact=True).evaluate("""(select, samples) => {
          window.__pushAudio(samples);
          select.value = 'en';
          select.dispatchEvent(new Event('change', {bubbles: true}));
        }""", tail_samples)
        await page.wait_for_function(
            "window.__nemotronRequests.some(r => r.action === 'startRecording' && r.sourceLang === 'en')"
        )
        sent = await page.evaluate("window.__nemotronRequests")
        restart = next(i for i, item in enumerate(sent)
                       if item["action"] == "startRecording" and item["sourceLang"] == "en")
        previous_audio = sent[restart - 1]
        assert previous_audio["action"] == "sendAudio"
        assert pcm(previous_audio["audioData"]) == b"\xff\x1f" * 800
        assert previous_audio["sourceLang"] == "ko", "The tail belongs to the previous recognition language"
        await page.get_by_role("button", name="녹음 일시정지", exact=True).click()
        assert len(await requests(page, "sendAudio")) == 2, "Settings flush must not duplicate audio on stop"
        assert not errors, errors
        print(f"PASS: short tail once on stop and language changes at {sample_rate} Hz; empty audio omitted", flush=True)
    finally:
        await context.close()


async def all_sample_rate_tails(browser):
    for sample_rate in (16000, 44100, 48000):
        await short_tail(browser, sample_rate)


async def provider_switch(browser):
    context, page, errors = await new_page(browser, manual_audio=True)
    try:
        await page.get_by_role("button", name="녹음 시작", exact=True).click()
        await expect(page.get_by_role("button", name="녹음 일시정지", exact=True)).to_be_visible()
        await advanced_settings(page)
        await page.get_by_role("button", name="Transcribe", exact=True).evaluate(
            "button => { window.__pushAudio(800); button.click(); }"
        )
        await page.keyboard.press("Escape")
        sent = await page.evaluate("window.__nemotronRequests")
        audio = [item for item in sent if item["action"] == "sendAudio"]
        assert len(audio) == 1 and len(pcm(audio[0]["audioData"])) == 1600
        assert sent.index(audio[0]) < next(i for i, item in enumerate(sent) if item["action"] == "stopRecording")
        await page.evaluate("""() => window.__nemotronSockets.findLast(s => s.readyState === 1).emit({
          type: 'subtitle_stream', phase: 'stt_partial', sttProvider: 'nemotron',
          streamId: 'old-recording', utteranceId: 1, messageId: 'partial',
          speaker: 'speaker1', originalText: '이전 엔진의 늦은 중간 결과입니다.',
          detectedLanguage: 'ko', timestamp: new Date().toISOString()
        })""")
        await page.wait_for_timeout(650)
        assert not await requests(page, "translateMessage"), "A late Nemotron partial was translated after switching engine"
        await page.get_by_role("button", name="녹음 시작", exact=True).click()
        await expect(page.get_by_role("button", name="녹음 일시정지", exact=True)).to_be_visible()
        await page.evaluate("""() => {
          const socket = window.__nemotronSockets.findLast(s => s.readyState === 1);
          socket.emit({type: 'subtitle_stream', phase: 'stt_partial', messageId: 'partial',
            speaker: 'speaker1', originalText: 'Current Transcribe speech.', detectedLanguage: 'en',
            timestamp: new Date().toISOString()});
          socket.emit({type: 'subtitle_stream', phase: 'stt', sttProvider: 'nemotron',
            streamId: 'old-recording', utteranceId: 1, messageId: 'previous-engine-final',
            speaker: 'speaker1', originalText: '이전 엔진의 확정 결과입니다.', detectedLanguage: 'ko',
            targetLanguage: 'en', timestamp: new Date().toISOString()});
        }""")
        await expect(page.get_by_text("Current Transcribe speech.", exact=True)).to_be_visible()
        await expect(page.get_by_text("이전 엔진의 확정 결과입니다.", exact=True)).to_be_visible()
        assert len(await requests(page, "translateMessage")) == 1, "Transcribe must retain partial translation"
        await expect(page.locator('[aria-label="현재 감지 언어"]:visible')).to_contain_text("English")
        assert not errors, errors
        print("PASS: engine switching preserves the tail and ignores old Nemotron partials", flush=True)
    finally:
        await context.close()


async def delayed_finals(browser):
    context, page, errors = await new_page(browser)
    try:
        await page.get_by_role("button", name="녹음 시작", exact=True).click()
        await expect(page.get_by_role("button", name="녹음 일시정지", exact=True)).to_be_visible()
        await page.evaluate("""() => {
          const socket = window.__nemotronSockets.findLast(s => s.readyState === 1);
          socket.emit({type: 'subtitle_stream', phase: 'stt_partial', sttProvider: 'nemotron',
            streamId: 'new-recording', utteranceId: 2, messageId: 'partial',
            speaker: 'speaker1', originalText: 'Current English speech.', detectedLanguage: 'en',
            timestamp: new Date().toISOString()});
          socket.emit({type: 'subtitle_stream', phase: 'stt', sttProvider: 'nemotron',
            streamId: 'new-recording', utteranceId: 1, messageId: 'older-utterance',
            speaker: 'speaker1', originalText: '이전의 확정된 발언입니다.', detectedLanguage: 'ko',
            targetLanguage: 'en', timestamp: new Date().toISOString()});
        }""")
        await expect(page.get_by_text("Current English speech.", exact=True)).to_be_visible()
        await expect(page.get_by_text("이전의 확정된 발언입니다.", exact=True)).to_be_visible()
        await expect(page.locator('[aria-label="현재 감지 언어"]:visible')).to_contain_text("English")
        for stream_id, utterance_id in (("old-recording", 2), ("new-recording", 1)):
            await page.evaluate("""data => window.__nemotronSockets.findLast(s => s.readyState === 1).emit({
              type: 'subtitle_stream', phase: 'stt_partial', sttProvider: 'nemotron',
              streamId: data.stream, utteranceId: data.utterance, messageId: 'partial',
              speaker: 'speaker1', originalText: '', timestamp: new Date().toISOString()
            })""", {"stream": stream_id, "utterance": utterance_id})
            await expect(page.get_by_text("Current English speech.", exact=True)).to_be_visible()
        await page.evaluate("""() => window.__nemotronSockets.findLast(s => s.readyState === 1).emit({
          type: 'subtitle_stream', phase: 'stt_partial', sttProvider: 'nemotron',
          streamId: 'new-recording', utteranceId: 2, messageId: 'partial',
          speaker: 'speaker1', originalText: '', timestamp: new Date().toISOString()
        })""")
        await expect(page.get_by_text("Current English speech.", exact=True)).to_have_count(0)
        assert not errors, errors
        print("PASS: delayed finals and clears preserve a newer utterances text and language", flush=True)
    finally:
        await context.close()


async def concurrent_translations(browser, completion_order, *, expire_display_timeout=False):
    context, page, errors = await new_page(browser, manual_audio=True)
    originals = {"a": "첫 번째 확정 발언", "b": "두 번째 확정 발언"}
    translations = {"a": "First finalized utterance.", "b": "Second finalized utterance."}

    async def emit(utterance, phase):
        await page.evaluate("""data => window.__nemotronSockets.findLast(s => s.readyState === 1).emit({
          type: 'subtitle_stream', phase: data.phase, sttProvider: 'nemotron',
          streamId: 'concurrent-translations', utteranceId: data.utterance === 'a' ? 1 : 2,
          messageId: 'concurrent-' + data.utterance, speaker: 'speaker1',
          originalText: data.original, translatedText: data.translation,
          detectedLanguage: 'ko', targetLanguage: 'en', timestamp: new Date().toISOString()
        })""", {"utterance": utterance, "phase": phase, "original": originals[utterance],
                  "translation": translations[utterance] if phase == "done" else None})

    try:
        await page.get_by_role("button", name="녹음 시작", exact=True).click()
        await emit("a", "stt")
        await expect(page.get_by_role("article").filter(has_text=originals["a"])).to_be_visible()
        if expire_display_timeout:
            # A display timeout is not evidence that its backend translation finished.
            await page.wait_for_timeout(15_100)
        await emit("b", "stt")
        await expect(page.get_by_role("article").filter(has_text=originals["b"])).to_be_visible()
        for utterance in completion_order:
            await emit(utterance, "done")
            await expect(page.get_by_role("article").filter(has_text=translations[utterance])).to_be_visible()
        for utterance in ("a", "b"):
            card = page.get_by_role("article").filter(has_text=originals[utterance])
            await expect(card).to_be_visible()
            await expect(card).to_contain_text(translations[utterance])
        assert not errors, errors
        suffix = " after the display timeout" if expire_display_timeout else ""
        print(f"PASS: both utterances retained for completion order {completion_order}{suffix}", flush=True)
    finally:
        await context.close()


async def completed_translations_merge(browser):
    context, page, errors = await new_page(browser, manual_audio=True)
    try:
        for index, original, translated in (
            (1, "이미 번역된 첫 발언", "The first completed translation."),
            (2, "그 뒤에 이어진 발언", "The next completed translation."),
        ):
            event = {
                "type": "subtitle_stream", "sttProvider": "nemotron",
                "streamId": "completed-translations", "utteranceId": index,
                "messageId": f"completed-{index}", "speaker": "speaker1",
                "originalText": original, "detectedLanguage": "ko", "targetLanguage": "en",
                "timestamp": "2026-10-28T10:00:00Z",
            }
            await page.evaluate(
                "event => window.__nemotronSockets.findLast(s => s.readyState === 1).emit(event)",
                {**event, "phase": "stt"},
            )
            await expect(page.get_by_role("article").filter(has_text=original)).to_be_visible()
            await page.evaluate(
                "event => window.__nemotronSockets.findLast(s => s.readyState === 1).emit(event)",
                {**event, "phase": "done", "translatedText": translated},
            )
            await expect(page.get_by_role("article").filter(has_text=translated)).to_be_visible()
        combined = page.get_by_role("article").filter(has_text="이미 번역된 첫 발언")
        await expect(combined).to_contain_text("그 뒤에 이어진 발언")
        await expect(combined).to_contain_text("The first completed translation. The next completed translation.")
        assert not errors, errors
        print("PASS: completed utterances retain the existing bubble-merging behavior", flush=True)
    finally:
        await context.close()


async def immediate_merged_translation(browser, *, same_language=False):
    context, page, errors = await new_page(browser, manual_audio=True)
    first, second = "이미 확정된 발언", "곧바로 번역되는 발언"
    first_translation = first if same_language else "Previously completed speech."
    second_translation = second if same_language else "Immediately completed translation."
    target = "ko" if same_language else "en"
    try:
        await page.get_by_role("combobox", name="번역 대상", exact=True).select_option(target)
        initial = {
            "type": "subtitle_stream", "sttProvider": "nemotron", "streamId": "immediate",
            "utteranceId": 1, "messageId": "completed-history", "speaker": "speaker1",
            "originalText": first, "detectedLanguage": "ko", "targetLanguage": target,
            "timestamp": "2026-10-28T10:00:00Z",
        }
        await page.evaluate(
            "event => window.__nemotronSockets.findLast(s => s.readyState === 1).emit(event)",
            {**initial, "phase": "stt"},
        )
        combined = page.get_by_role("article").filter(has_text=first)
        await expect(combined).to_be_visible()
        await page.evaluate(
            "event => window.__nemotronSockets.findLast(s => s.readyState === 1).emit(event)",
            {**initial, "phase": "done", "translatedText": first_translation},
        )
        await expect(combined.get_by_text(first_translation, exact=True)).to_have_count(
            2 if same_language else 1
        )

        # Native streaming can deliver partial/STT/final before React commits
        # any of these updates, especially for same-language translation.
        following = {**initial, "utteranceId": 2, "messageId": "immediate-result",
                     "originalText": second, "translatedText": second_translation}
        await page.evaluate("""event => {
          const socket = window.__nemotronSockets.findLast(s => s.readyState === 1);
          socket.emit({...event, phase: 'stt_partial', messageId: 'partial'});
          socket.emit({...event, phase: 'stt'});
          socket.emit({...event, phase: 'done'});
        }""", following)
        original = first + " " + second
        translation = first_translation + " " + second_translation
        await expect(combined.get_by_text(original, exact=True)).to_have_count(
            2 if same_language else 1
        )
        if not same_language:
            await expect(combined.get_by_text(translation, exact=True)).to_have_count(1)
            # Serialize while a merged utterance is still pending, when its
            # completion identity exists in the actual React message state.
            await page.evaluate(
                "event => window.__nemotronSockets.findLast(s => s.readyState === 1).emit(event)",
                {**following, "phase": "stt", "messageId": "pending-summary",
                 "utteranceId": 3, "originalText": "요약에 포함할 진행 중 발언"},
            )
            await expect(combined).to_contain_text("요약에 포함할 진행 중 발언")
            await page.get_by_role("button", name="요약 생성", exact=True).click()
            await page.wait_for_function(
                "window.__nemotronRequests.some(r => r.action === 'summarize')"
            )
            summary = (await requests(page, "summarize"))[-1]
            assert any("요약에 포함할 진행 중 발언" in item["original"] for item in summary["messages"])
            assert all(set(item) <= {"speaker", "original", "timestamp"} for item in summary["messages"])
        assert not errors, errors
        print(f"PASS: same-turn merged final retains translation; same language={same_language}", flush=True)
    finally:
        await context.close()


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=[
            "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
        ])
        try:
            scenarios = {
                "settings": settings_and_capture,
                "partials": partials_and_finals,
                "tail": all_sample_rate_tails,
                "switch": provider_switch,
                "finals": delayed_finals,
                "translations_ab": lambda browser: concurrent_translations(browser, ("a", "b")),
                "translations_ba": lambda browser: concurrent_translations(browser, ("b", "a")),
                "translations_timeout": lambda browser: concurrent_translations(
                    browser, ("b", "a"), expire_display_timeout=True
                ),
                "translations_completed": completed_translations_merge,
                "translations_immediate": immediate_merged_translation,
                "translations_same_language": lambda browser: immediate_merged_translation(
                    browser, same_language=True
                ),
            }
            selected = os.environ.get("TRANSMEET_TEST_CASE")
            for name, scenario in scenarios.items():
                if selected is None or selected == name:
                    await scenario(browser)
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
