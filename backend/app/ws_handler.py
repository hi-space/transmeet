"""
WebSocket audio pipeline handler.

Transcribe mode uses a **persistent streaming session** for the entire recording:
  - On startRecording: open one Transcribe stream that stays open
  - On sendAudio: PCM chunks are fed directly into the stream (no buffering)
  - Partial results → frontend immediately as { phase: "stt_partial" }
  - Final results   → Bedrock translation → { phase: "stt" / "translating" / "done" }
  - On stopRecording: send end-of-stream, wait for remaining results

Whisper mode keeps the original buffer/flush pipeline:
  - Audio chunks are buffered per connection
  - After 600ms of silence (or buffer full), the utterance is flushed to SageMaker

Message sequence per final utterance (both modes):
  1. { type: "subtitle_stream", phase: "stt",         originalText }
  2. { type: "subtitle_stream", phase: "translating", partialTranslation } × N tokens
  3. { type: "subtitle_stream", phase: "done",        originalText, translatedText, detectedLanguage }

Transcribe-only partial:
  0. { type: "subtitle_stream", phase: "stt_partial", originalText }  (live, not committed)

Inbound message format:
{
  action: "sendAudio" | "startRecording" | "stopRecording" | "ping",
  audioData?: "<base64 WAV>",
  meetingId?: "<uuid>",
  speaker?: "remote" | "local",
  sourceLang?: "ko" | "en" | "auto",
  targetLang?: "ko" | "en",
  modelId?: string,
  sttProvider?: "whisper" | "transcribe",
}
"""
import asyncio
import base64
import json
import logging
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.model import TranscriptEvent as TranscribeEvent
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .aws_clients import (
    SILENCE_RMS_THRESHOLD,
    bedrock_client,
    compute_rms,
    dynamodb_client,
    generate_message_id,
    is_hallucination,
    normalize_language,
    polly_client,
    sagemaker_client,
)
from .config import config

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_BUFFER_BYTES = 512_000   # ~8 seconds at 16 kHz 16-bit mono
FLUSH_DELAY_SECS = 0.6       # 600 ms silence → flush (Whisper mode only)
SEGMENT_MERGE_DELAY_SECS = 2.0  # Transcribe: wait this long before translating (accumulates consecutive finals)

# Transcribe language code mapping
LANG_CODE_MAP: dict = {
    "en": "en-US",
    "ko": "ko-KR",
}

# Language codes that support ShowSpeakerLabel in Transcribe Streaming
# (ko-KR does NOT support streaming speaker diarization)
DIARIZATION_SUPPORTED_LANGS = {"en-US", "de-DE", "es-US", "fr-FR", "it-IT", "pt-BR"}

# Map Transcribe speaker labels (spk_0, spk_1, …) to app speaker keys
SPEAKER_LABEL_MAP = {"spk_0": "speaker1", "spk_1": "speaker2"}


def _speaker_key(label: str) -> str:
    return SPEAKER_LABEL_MAP.get(label, "speaker2")


@dataclass
class ConnectionState:
    ws: WebSocket
    meeting_id: Optional[str] = None
    stt_provider: str = "whisper"
    target_lang: str = "ko"
    source_lang: str = "auto"
    model_id: str = field(default_factory=lambda: config.BEDROCK_MODEL_ID)
    speaker: str = "speaker1"
    # Transcribe: final 결과로 확인된 마지막 화자 (partial에서 provisional로 사용)
    last_confirmed_speaker: str = "speaker1"
    # Whisper mode: buffer/flush
    audio_buffer: List[bytes] = field(default_factory=list)
    flush_task: Optional[asyncio.Task] = None
    # Transcribe mode: persistent streaming session
    audio_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    transcribe_task: Optional[asyncio.Task] = None
    # Transcribe mode: deferred segment accumulation (merges consecutive finals before translating)
    seg_buffer: List[tuple] = field(default_factory=list)  # [(text, speaker, detected_lang)]
    seg_flush_task: Optional[asyncio.Task] = None
    seg_last_speaker: Optional[str] = None  # 버퍼에 있는 마지막 화자 (화자 변경 감지용)
    # Translation timing: "sentence" | "realtime" | "manual"
    translation_timing: str = "sentence"


# In-memory connection registry (single-process; scales with one Fargate task)
active_connections: Dict[str, ConnectionState] = {}


_ws_send_locks: Dict[int, asyncio.Lock] = {}


async def _safe_send(ws: WebSocket, data: dict) -> None:
    """Send JSON via WebSocket with a per-connection lock.

    Starlette WebSocket does not support concurrent send_json calls;
    without serialization, concurrent tasks (translation + summary) can
    corrupt frames or silently block.
    """
    ws_id = id(ws)
    lock = _ws_send_locks.get(ws_id)
    if lock is None:
        lock = asyncio.Lock()
        _ws_send_locks[ws_id] = lock
    async with lock:
        await ws.send_json(data)  # raw send inside lock


def _cleanup_ws_lock(ws: WebSocket) -> None:
    _ws_send_locks.pop(id(ws), None)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def combine_wav_chunks(chunks: List[bytes]) -> bytes:
    """Combine multiple WAV chunks (44-byte header + PCM) into a single WAV."""
    pcm = b"".join(c[44:] for c in chunks)
    num_channels = 1
    sample_rate = 16000
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,            # PCM format
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + pcm


def _extract_segments(
    alt, use_speaker_labels: bool, default_speaker: str
) -> list[tuple[str, str]]:
    """
    Parse a Transcribe alternative into (text, speaker_key) segments.

    When speaker labels are available, groups consecutive word-level items
    by speaker. Otherwise returns a single segment with default_speaker.
    """
    items = alt.items or []
    transcript_text = (alt.transcript or "").strip()

    logger.debug(
        "[_extract_segments] use_speaker_labels=%s items_count=%d transcript=%r",
        use_speaker_labels, len(items), transcript_text,
    )

    if not use_speaker_labels or not items:
        return [(transcript_text, default_speaker)] if transcript_text else []

    segments: list[tuple[str, str]] = []
    current_label: Optional[str] = None
    current_words: list[str] = []
    for item in items:
        itype = getattr(item, "item_type", "") or ""
        content = getattr(item, "content", "") or ""
        label = getattr(item, "speaker", None) or "spk_0"
        if itype == "punctuation":
            if current_words:
                current_words[-1] += content
        elif itype == "pronunciation":
            if current_label is None:
                current_label = label
            if label != current_label:
                text = " ".join(current_words).strip()
                if text:
                    segments.append((text, _speaker_key(current_label)))
                current_label = label
                current_words = []
            current_words.append(content)
    if current_words and current_label is not None:
        text = " ".join(current_words).strip()
        if text:
            segments.append((text, _speaker_key(current_label)))
    logger.debug("[_extract_segments] result segments=%s", [(t[:30], s) for t, s in segments])
    return segments


def _merge_consecutive_same_speaker(
    segments: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Merge adjacent segments from the same speaker into one."""
    merged: list[tuple[str, str]] = []
    for text, speaker in segments:
        if merged and merged[-1][1] == speaker:
            merged[-1] = (merged[-1][0] + " " + text, speaker)
        else:
            merged.append((text, speaker))
    return merged


async def _process_segment(
    ws: WebSocket,
    original_text: str,
    speaker: str,
    detected_language: str,
    state: ConnectionState,
    timestamp: str,
) -> None:
    """
    Run the translation pipeline for one speech segment and emit WS events.

    Steps: push STT result → stream Bedrock translation → push final subtitle
           → persist to DynamoDB.
    """
    message_id = generate_message_id()
    meeting_id = state.meeting_id
    bedrock_model_id = state.model_id

    translation_target = state.target_lang or "ko"
    logger.info(
        "[process_segment] speaker=%s detectedLang=%s -> translation_target=%s text=%r",
        speaker, detected_language, translation_target, original_text,
    )
    source_lang_label = "Korean" if detected_language == "ko" else "English"
    target_lang_label = "Korean" if translation_target == "ko" else "English"

    # ── 1: Push STT result immediately ─────────────────────────────────────────
    await _safe_send(ws, {
        "type": "subtitle_stream",
        "messageId": message_id,
        "phase": "stt",
        "speaker": speaker,
        "originalText": original_text,
        "timestamp": timestamp,
    })

    # ── 2: Stream translation via Bedrock ──────────────────────────────────────
    prompt = (
        f"Translate the following {source_lang_label} text to {target_lang_label}. "
        f"Output only the translated text, no explanations, no quotes.\n\n"
        f"Text: {original_text}"
    )

    translated_text = ""
    try:
        async with bedrock_client() as br:
            stream_resp = await br.converse_stream(
                modelId=bedrock_model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 1024},
            )
            async for event in stream_resp["stream"]:
                delta = event.get("contentBlockDelta", {}).get("delta", {}).get("text")
                if not delta:
                    continue
                translated_text += delta
                await _safe_send(ws, {
                    "type": "subtitle_stream",
                    "messageId": message_id,
                    "phase": "translating",
                    "speaker": speaker,
                    "originalText": original_text,
                    "partialTranslation": translated_text,
                    "timestamp": timestamp,
                })
    except Exception:
        logger.exception("[process_segment] Bedrock translation failed for message_id=%s", message_id)
        # 번역 실패해도 done phase는 보내야 프론트엔드 말풍선이 stuck 안 됨

    # ── 3: Push final subtitle ─────────────────────────────────────────────────
    await _safe_send(ws, {
        "type": "subtitle_stream",
        "messageId": message_id,
        "phase": "done",
        "speaker": speaker,
        "originalText": original_text,
        "translatedText": translated_text,
        "detectedLanguage": detected_language,
        "timestamp": timestamp,
    })

    # ── 4: Persist to DynamoDB ─────────────────────────────────────────────────
    if meeting_id:
        async with dynamodb_client() as ddb:
            await ddb.update_item(
                TableName=config.MEETINGS_TABLE,
                Key={"meetingId": {"S": meeting_id}},
                UpdateExpression=(
                    "SET messages = list_append(if_not_exists(messages, :empty), :msg), "
                    "#updatedAt = :ts "
                    "ADD messageCount :one"
                ),
                ExpressionAttributeNames={"#updatedAt": "updatedAt"},
                ExpressionAttributeValues={
                    ":msg": {"L": [{"M": {
                        "id": {"S": message_id},
                        "speaker": {"S": speaker},
                        "originalText": {"S": original_text},
                        "translatedText": {"S": translated_text},
                        "detectedLanguage": {"S": detected_language},
                        "timestamp": {"S": timestamp},
                    }}]},
                    ":empty": {"L": []},
                    ":ts": {"S": timestamp},
                    ":one": {"N": "1"},
                },
            )


def _apply_segment_filters(
    text: str, source_lang: Optional[str]
) -> bool:
    """Return True if segment should be processed (not filtered)."""
    if is_hallucination(text):
        logger.info("[ws-audio] Hallucination filtered: %r", text)
        return False
    if source_lang == "en":
        non_ascii = sum(1 for c in text if ord(c) > 127)
        if non_ascii / max(len(text), 1) > 0.3:
            logger.info(
                "[ws-audio] Mis-detection filtered (%.0f%% non-ASCII): %r",
                100 * non_ascii / len(text),
                text,
            )
            return False
    return True


async def _retranslate_segment(
    ws: WebSocket,
    message_id: str,
    original_text: str,
    speaker: str,
    detected_language: str,
    target_lang: str,
    model_id: str,
    timestamp: str,
) -> None:
    """
    Re-translate a segment using an existing message_id.
    Skips the STT phase (uses provided text directly) and DynamoDB write.
    Streams translating + done phases back to the frontend.
    """
    translation_target = target_lang if target_lang else "ko"
    logger.info(
        "[retranslate] messageId=%s speaker=%s detectedLang=%s -> translation_target=%s text=%r",
        message_id, speaker, detected_language, translation_target, original_text,
    )
    source_lang_label = "Korean" if detected_language == "ko" else "English"
    target_lang_label = "Korean" if translation_target == "ko" else "English"

    prompt = (
        f"Translate the following {source_lang_label} text to {target_lang_label}. "
        f"Output only the translated text, no explanations, no quotes.\n\n"
        f"Text: {original_text}"
    )

    translated_text = ""
    async with bedrock_client() as br:
        stream_resp = await br.converse_stream(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 1024},
        )
        async for event in stream_resp["stream"]:
            delta = event.get("contentBlockDelta", {}).get("delta", {}).get("text")
            if not delta:
                continue
            translated_text += delta
            await _safe_send(ws, {
                "type": "subtitle_stream",
                "messageId": message_id,
                "phase": "translating",
                "speaker": speaker,
                "originalText": original_text,
                "partialTranslation": translated_text,
                "timestamp": timestamp,
            })

    logger.info("[retranslate] done: translatedText=%r", translated_text)
    await _safe_send(ws, {
        "type": "subtitle_stream",
        "messageId": message_id,
        "phase": "done",
        "speaker": speaker,
        "originalText": original_text,
        "translatedText": translated_text,
        "detectedLanguage": detected_language,
        "timestamp": timestamp,
    })


async def _flush_seg_buffer(state: "ConnectionState", ws: WebSocket) -> None:
    """
    Translate all accumulated Transcribe final segments at once.

    Consecutive segments from the same speaker (and same detected language) are
    merged into a single text before being sent to Bedrock, reducing API calls
    and producing more natural translations.
    """
    if not state.seg_buffer:
        return
    buf = state.seg_buffer[:]
    state.seg_buffer.clear()
    state.seg_last_speaker = None

    # Merge consecutive same-speaker, same-lang entries
    merged: List[tuple] = []
    for text, speaker, lang in buf:
        if merged and merged[-1][1] == speaker and merged[-1][2] == lang:
            merged[-1] = (merged[-1][0] + " " + text, speaker, lang)
        else:
            merged.append((text, speaker, lang))

    timestamp = _iso_now()
    for text, speaker, lang in merged:
        asyncio.create_task(_process_segment(ws, text, speaker, lang, state, timestamp))


# ─── Transcribe: persistent streaming session ────────────────────────────────

async def _run_transcribe_streaming(
    state: ConnectionState,
    ws: WebSocket,
    connection_id: str,
) -> None:
    """
    Persistent AWS Transcribe Streaming session for one recording.

    Lifecycle:
      startRecording → this task is created
      sendAudio      → PCM chunks are put into state.audio_queue
      stopRecording  → None sentinel is put into state.audio_queue

    Partial results are sent to the frontend immediately (live pending bubble).
    Final results are routed through the translation pipeline.
    """
    lang_code = state.source_lang
    mapped_lang = (
        LANG_CODE_MAP.get(lang_code)
        if lang_code not in ("auto", "", None)
        else None
    )
    use_speaker_labels = mapped_lang in DIARIZATION_SUPPORTED_LANGS if mapped_lang else False

    kwargs: dict = {"media_sample_rate_hz": 16000, "media_encoding": "pcm"}
    if lang_code in ("auto", "", None):
        kwargs["identify_language"] = True
        kwargs["language_options"] = "en-US,ko-KR"
    else:
        kwargs["language_code"] = mapped_lang
        if use_speaker_labels:
            kwargs["show_speaker_label"] = True

    try:
        client = TranscribeStreamingClient(region=config.REGION)
        stream = await client.start_stream_transcription(**kwargs)
        logger.info(
            "[Transcribe] Session started: connection_id=%s sourceLang=%s mapped_lang=%s "
            "show_speaker_label=%s kwargs_keys=%s",
            connection_id, lang_code, mapped_lang or "auto",
            use_speaker_labels, list(kwargs.keys()),
        )
    except Exception:
        logger.exception("[Transcribe] Failed to start session for connection_id=%s", connection_id)
        return

    detected_lang: str = mapped_lang[:2].lower() if mapped_lang else "en"

    async def _send_audio() -> None:
        try:
            while True:
                chunk = await state.audio_queue.get()
                if chunk is None:           # sentinel → end of recording
                    await stream.input_stream.end_stream()
                    break
                await stream.input_stream.send_audio_event(audio_chunk=chunk)
        except asyncio.CancelledError:
            await stream.input_stream.end_stream()
            raise
        except Exception:
            logger.exception("[Transcribe] _send_audio error for connection_id=%s", connection_id)
            await stream.input_stream.end_stream()

    async def _collect_results() -> None:
        nonlocal detected_lang
        # TranscriptResultStream은 AsyncIterable이므로 __aiter__()로 iterator를 먼저 획득
        output_iter = stream.output_stream.__aiter__()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        output_iter.__anext__(),
                        timeout=30.0,
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    logger.warning(
                        "[Transcribe] Stream idle 30s — ending session for connection_id=%s",
                        connection_id,
                    )
                    break
                if not isinstance(event, TranscribeEvent):
                    continue
                for result in event.transcript.results:
                    lc = getattr(result, "language_code", None) or ""
                    if lc:
                        detected_lang = lc[:2].lower()

                    alts = result.alternatives or []
                    if not alts:
                        continue
                    alt = alts[0]
                    transcript_text = (alt.transcript or "").strip()
                    if not transcript_text:
                        continue

                    if result.is_partial:
                        # Live partial → pending bubble on frontend (not committed)
                        # last_confirmed_speaker: 마지막 final에서 확인된 화자 사용
                        # (첫 발화 전엔 default "speaker1", 이후엔 실제 화자 반영)
                        try:
                            await _safe_send(ws, {
                                "type": "subtitle_stream",
                                "phase": "stt_partial",
                                "messageId": "partial",
                                "speaker": state.last_confirmed_speaker,
                                "originalText": transcript_text,
                                "timestamp": _iso_now(),
                            })
                        except Exception:
                            pass  # WS may be closing
                    else:
                        # Final sentence → accumulate for deferred translation
                        segments = _extract_segments(alt, use_speaker_labels, state.speaker)
                        segments = _merge_consecutive_same_speaker(segments)
                        logger.info(
                            "[Transcribe] Final result: transcript=%r segments=%s",
                            transcript_text, [(t[:40], s) for t, s in segments],
                        )
                        # partial speaker를 다음 발화에 반영하기 위해 마지막 화자 저장
                        if segments:
                            state.last_confirmed_speaker = segments[-1][1]

                        # Language for translation
                        if state.source_lang not in ("auto", "", None):
                            language = normalize_language(state.source_lang)
                        else:
                            language = normalize_language(detected_lang)

                        for text, speaker in segments:
                            if not _apply_segment_filters(text, state.source_lang):
                                continue
                            # 화자가 바뀌면 기존 버퍼를 즉시 flush 후 새 화자로 시작
                            if (
                                state.seg_buffer
                                and state.seg_last_speaker is not None
                                and state.seg_last_speaker != speaker
                            ):
                                if state.seg_flush_task and not state.seg_flush_task.done():
                                    state.seg_flush_task.cancel()
                                    state.seg_flush_task = None
                                await _flush_seg_buffer(state, ws)
                            state.seg_buffer.append((text, speaker, language))
                            state.seg_last_speaker = speaker

                        timing = state.translation_timing

                        if timing == "manual":
                            # 자동 번역 없음 — translateMessage action으로만 번역
                            pass
                        elif timing == "realtime":
                            # 모든 세그먼트마다 즉시 번역
                            if state.seg_flush_task and not state.seg_flush_task.done():
                                state.seg_flush_task.cancel()
                            await _flush_seg_buffer(state, ws)
                        else:
                            # sentence(기본): 문장 종료 부호 감지 or 2초 대기
                            sentence_ended = any(
                                (t.rstrip() or "")[-1:] in ".?!。？！"
                                for t, _ in segments
                                if _apply_segment_filters(t, state.source_lang)
                            )
                            if sentence_ended:
                                if state.seg_flush_task and not state.seg_flush_task.done():
                                    state.seg_flush_task.cancel()
                                await _flush_seg_buffer(state, ws)
                            else:
                                if state.seg_flush_task and not state.seg_flush_task.done():
                                    state.seg_flush_task.cancel()

                                async def _deferred_flush(s=state, w=ws) -> None:
                                    try:
                                        await asyncio.sleep(SEGMENT_MERGE_DELAY_SECS)
                                        await _flush_seg_buffer(s, w)
                                    except asyncio.CancelledError:
                                        pass

                                state.seg_flush_task = asyncio.create_task(_deferred_flush())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[Transcribe] _collect_results error for connection_id=%s", connection_id)
        finally:
            # output stream이 어떤 이유로 끝나든 _send_audio()가 queue.get()에 블록되지 않도록 sentinel 전송
            # → gather()가 완료되고 transcribe_task.done()이 True → 다음 sendAudio에서 자동 재시작 발동
            try:
                state.audio_queue.put_nowait(None)
            except Exception:
                pass

    try:
        await asyncio.gather(_send_audio(), _collect_results())
        logger.info("[Transcribe] Session ended: connection_id=%s", connection_id)
    except asyncio.CancelledError:
        logger.info("[Transcribe] Session cancelled: connection_id=%s", connection_id)
    except Exception:
        logger.exception("[Transcribe] Session failed for connection_id=%s", connection_id)


# ─── Whisper: buffer/flush pipeline ─────────────────────────────────────────

async def _process_whisper_audio(
    ws: WebSocket, audio_bytes: bytes, state: ConnectionState, connection_id: str
) -> None:
    """Send one buffered utterance to SageMaker Whisper and run translation."""
    timestamp = _iso_now()
    req_source_lang: Optional[str] = None if state.source_lang == "auto" else state.source_lang

    async with sagemaker_client() as sm:
        whisper_resp = await sm.invoke_endpoint(
            EndpointName=config.WHISPER_ENDPOINT,
            ContentType="audio/wav",
            Body=audio_bytes,
        )
        whisper_body = await whisper_resp["Body"].read()

    whisper_result = json.loads(whisper_body.decode("utf-8"))
    raw_text = whisper_result.get("text", "")
    original_text = (" ".join(raw_text) if isinstance(raw_text, list) else raw_text).strip()
    detected_lang_raw = whisper_result.get("language", "")
    logger.info("[ws-audio] Whisper result: text=%r language=%r", original_text, detected_lang_raw)

    if not original_text:
        return

    if req_source_lang:
        detected_language = normalize_language(req_source_lang)
    else:
        detected_language = normalize_language(detected_lang_raw)

    if not _apply_segment_filters(original_text, req_source_lang):
        return

    await _process_segment(ws, original_text, state.speaker, detected_language, state, timestamp)


async def _flush_audio_buffer(
    state: ConnectionState, ws: WebSocket, connection_id: str
) -> None:
    """Flush accumulated audio buffer to SageMaker Whisper as a single utterance."""
    if not state.audio_buffer:
        return
    chunks = state.audio_buffer[:]
    state.audio_buffer.clear()
    combined_wav = combine_wav_chunks(chunks)
    try:
        await _process_whisper_audio(ws, combined_wav, state, connection_id)
    except Exception:
        logger.exception("[ws] Audio buffer flush failed for connection_id=%s", connection_id)
        try:
            await _safe_send(ws, {
                "type": "error",
                "message": "Processing failed. Please try again.",
                "timestamp": _iso_now(),
            })
        except Exception:
            pass


async def _schedule_flush(
    state: ConnectionState, ws: WebSocket, connection_id: str
) -> None:
    """Cancel any pending flush timer and reschedule after FLUSH_DELAY_SECS."""
    if state.flush_task and not state.flush_task.done():
        state.flush_task.cancel()

    async def _do_flush() -> None:
        try:
            await asyncio.sleep(FLUSH_DELAY_SECS)
            await _flush_audio_buffer(state, ws, connection_id)
        except asyncio.CancelledError:
            pass

    state.flush_task = asyncio.create_task(_do_flush())


# ─── TTS request streaming ───────────────────────────────────────────────────

async def _handle_tts_request(
    ws: WebSocket,
    message_id: str,
    text: str,
    model_id: str,
    polly_engine: str,
    polly_voice_id: str,
    timestamp: str,
    meeting_id: str = "",
) -> None:
    """KO→EN 번역 스트리밍 후 Polly TTS 합성."""
    prompt = (
        f"Translate the following Korean text to natural, fluent English. "
        f"Return only the translated text.\n\n{text}"
    )

    # Step 1: Stream Bedrock translation
    translated_text = ""
    try:
        async with bedrock_client() as br:
            stream_resp = await br.converse_stream(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 1024},
            )
            async for event in stream_resp["stream"]:
                delta = event.get("contentBlockDelta", {}).get("delta", {}).get("text")
                if not delta:
                    continue
                translated_text += delta
                await _safe_send(ws, {
                    "type": "tts_stream",
                    "messageId": message_id,
                    "phase": "translating",
                    "partialText": translated_text,
                    "timestamp": timestamp,
                })
    except Exception:
        logger.exception("[tts_request] Bedrock streaming failed for messageId=%s", message_id)
        try:
            await _safe_send(ws, {
                "type": "tts_stream",
                "messageId": message_id,
                "phase": "error",
                "timestamp": timestamp,
            })
        except Exception:
            pass
        return

    if not translated_text:
        return

    # Step 2: Polly TTS synthesis
    try:
        # Seoyeon(Korean) only supports neural engine
        actual_engine = "neural" if polly_voice_id == "Seoyeon" and polly_engine != "neural" else polly_engine
        async with polly_client() as polly:
            polly_resp = await polly.synthesize_speech(
                Text=translated_text,
                OutputFormat="mp3",
                VoiceId=polly_voice_id,
                Engine=actual_engine,
            )
            audio_bytes = await polly_resp["AudioStream"].read()
        audio_data = base64.b64encode(audio_bytes).decode("utf-8")

        await _safe_send(ws, {
            "type": "tts_stream",
            "messageId": message_id,
            "phase": "done",
            "translatedText": translated_text,
            "audioData": audio_data,
            "timestamp": timestamp,
        })
    except Exception:
        logger.exception("[tts_request] Polly synthesis failed for messageId=%s", message_id)
        # Still send done without audio so frontend can show the translation
        await _safe_send(ws, {
            "type": "tts_stream",
            "messageId": message_id,
            "phase": "done",
            "translatedText": translated_text,
            "timestamp": timestamp,
        })

    # ── Persist to DynamoDB ────────────────────────────────────────────────────
    # speaker="me": original=EN(translated), translation=KO(source text)
    if meeting_id and translated_text:
        try:
            async with dynamodb_client() as ddb:
                await ddb.update_item(
                    TableName=config.MEETINGS_TABLE,
                    Key={"meetingId": {"S": meeting_id}},
                    UpdateExpression=(
                        "SET messages = list_append(if_not_exists(messages, :empty), :msg), "
                        "#updatedAt = :ts "
                        "ADD messageCount :one"
                    ),
                    ExpressionAttributeNames={"#updatedAt": "updatedAt"},
                    ExpressionAttributeValues={
                        ":msg": {"L": [{"M": {
                            "id": {"S": message_id},
                            "speaker": {"S": "me"},
                            "originalText": {"S": translated_text},
                            "translatedText": {"S": text},
                            "detectedLanguage": {"S": "ko"},
                            "timestamp": {"S": timestamp},
                        }}]},
                        ":empty": {"L": []},
                        ":ts": {"S": timestamp},
                        ":one": {"N": "1"},
                    },
                )
        except Exception:
            logger.exception("[tts_request] DynamoDB save failed for messageId=%s", message_id)


# ─── Summary streaming ───────────────────────────────────────────────────────

async def _stream_summary(
    ws: WebSocket,
    meeting_id: str,
    model_id: str,
    *,
    inline_messages: Optional[list] = None,
) -> None:
    """Fetch meeting messages and stream a summary via converse_stream.

    If inline_messages is provided (from frontend), use those directly
    instead of reading from DynamoDB. This ensures summaries work during
    live recording when messages may not yet be persisted.
    """
    logger.warning("[_stream_summary] START meetingId=%s inline=%s model=%s", meeting_id, len(inline_messages) if inline_messages else 0, model_id)
    try:
        lines: List[str] = []
        if inline_messages:
            for m in inline_messages:
                speaker = m.get("speaker", "")
                orig = m.get("original", "")
                if orig:
                    lines.append(f"[{speaker}] {orig}")
        else:
            async with dynamodb_client() as ddb:
                result = await ddb.get_item(
                    TableName=config.MEETINGS_TABLE,
                    Key={"meetingId": {"S": meeting_id}},
                )
            raw_messages = result.get("Item", {}).get("messages", {}).get("L", [])
            for m in raw_messages:
                mv = m.get("M", {})
                speaker = mv.get("speaker", {}).get("S", "")
                orig = mv.get("originalText", {}).get("S", "")
                if orig:
                    lines.append(f"[{speaker}] {orig}")

        if not lines:
            await _safe_send(ws, {"type": "summary_stream", "phase": "error", "error": "No messages to summarize"})
            return
        transcript = "\n".join(lines)

        system_prompt = "You are a professional meeting summarizer. Analyze meeting transcripts and produce clear, structured Korean summaries."
        user_prompt = (
            "Summarize the following meeting transcript in Korean. Include:\n"
            "- 개요: one-sentence overview of the meeting\n"
            "- 핵심 메시지: 1-2 most important takeaways\n"
            "- 주요 포인트: key discussion items\n"
            "- 상세 노트: any additional details worth noting\n\n"
            "Be concise but comprehensive. Do not omit important information.\n\n"
            f"---\n{transcript}"
        )

        logger.warning("[_stream_summary] calling Bedrock with %d lines", len(lines))
        summary = ""
        async with bedrock_client() as br:
            stream_resp = await br.converse_stream(
                modelId=model_id,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                inferenceConfig={"maxTokens": 4096},
            )
            first_delta = True
            async for event in stream_resp["stream"]:
                delta = event.get("contentBlockDelta", {}).get("delta", {}).get("text")
                if not delta:
                    continue
                if first_delta:
                    logger.warning("[_stream_summary] first delta received")
                    first_delta = False
                summary += delta
                await _safe_send(ws, {"type": "summary_stream", "phase": "delta", "text": delta})

        logger.warning("[_stream_summary] done, summary_len=%d", len(summary))
        async with dynamodb_client() as ddb:
            await ddb.update_item(
                TableName=config.MEETINGS_TABLE,
                Key={"meetingId": {"S": meeting_id}},
                UpdateExpression="SET summary = :s, summarizedAt = :t",
                ExpressionAttributeValues={
                    ":s": {"S": summary},
                    ":t": {"S": _iso_now()},
                },
            )

        await _safe_send(ws, {"type": "summary_stream", "phase": "done", "summary": summary})

    except Exception:
        logger.exception("[ws] _stream_summary failed for meeting_id=%s", meeting_id)
        try:
            await _safe_send(ws, {"type": "summary_stream", "phase": "error", "error": "Summary generation failed"})
        except Exception:
            pass


# ─── WebSocket endpoint ──────────────────────────────────────────────────────

@router.websocket("/ws")
async def ws_endpoint(
    ws: WebSocket,
    meetingId: Optional[str] = None,
):
    await ws.accept()
    connection_id = str(uuid4())
    state = ConnectionState(ws=ws, meeting_id=meetingId)
    active_connections[connection_id] = state
    logger.info("[ws] Connected: connection_id=%s meetingId=%s", connection_id, meetingId)

    try:
        while True:
            data = await ws.receive_json()
            action = data.get("action")
            if action not in ("ping", "sendAudio"):
                logger.warning("[ws] action=%s connection_id=%s", action, connection_id)

            if action == "ping":
                await _safe_send(ws, {"type": "pong", "timestamp": _iso_now()})

            elif action == "startRecording":
                # ── Cancel / reset any previous session ──────────────────────
                if state.transcribe_task and not state.transcribe_task.done():
                    state.transcribe_task.cancel()
                    state.transcribe_task = None
                if state.flush_task and not state.flush_task.done():
                    state.flush_task.cancel()
                    state.flush_task = None
                state.audio_buffer.clear()
                # Fresh queue for this session
                state.audio_queue = asyncio.Queue()

                state.stt_provider = data.get("sttProvider", "whisper")
                state.target_lang = data.get("targetLang", "ko") or "ko"
                state.source_lang = data.get("sourceLang", "auto") or "auto"
                state.model_id = data.get("modelId") or config.BEDROCK_MODEL_ID
                state.meeting_id = data.get("meetingId") or state.meeting_id
                state.speaker = data.get("speaker", "speaker1")
                state.translation_timing = data.get("translationTiming", "sentence") or "sentence"
                state.seg_buffer.clear()
                state.seg_last_speaker = None
                state.last_confirmed_speaker = "speaker1"
                if state.seg_flush_task and not state.seg_flush_task.done():
                    state.seg_flush_task.cancel()
                    state.seg_flush_task = None

                # startRecording 시 diarization 가능 여부 미리 계산해서 로그
                _dbg_mapped = (
                    LANG_CODE_MAP.get(state.source_lang)
                    if state.source_lang not in ("auto", "", None)
                    else None
                )
                _dbg_diarize = _dbg_mapped in DIARIZATION_SUPPORTED_LANGS if _dbg_mapped else False
                logger.info(
                    "[ws] startRecording: provider=%s sourceLang=%s mapped_lang=%s "
                    "use_speaker_labels=%s targetLang=%s speaker=%s",
                    state.stt_provider, state.source_lang, _dbg_mapped,
                    _dbg_diarize, state.target_lang, state.speaker,
                )

                if state.stt_provider == "transcribe":
                    # Open persistent streaming session for entire recording
                    state.transcribe_task = asyncio.create_task(
                        _run_transcribe_streaming(state, ws, connection_id)
                    )

            elif action == "stopRecording":
                if state.stt_provider == "transcribe":
                    if state.transcribe_task and not state.transcribe_task.done():
                        # Signal end-of-stream; wait up to 8 s for final results
                        await state.audio_queue.put(None)
                        try:
                            await asyncio.wait_for(state.transcribe_task, timeout=8.0)
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            state.transcribe_task.cancel()
                        state.transcribe_task = None
                    # Flush any segments still pending in the buffer (bypass 1.5s delay)
                    if state.seg_flush_task and not state.seg_flush_task.done():
                        state.seg_flush_task.cancel()
                        state.seg_flush_task = None
                    await _flush_seg_buffer(state, ws)
                else:
                    # Whisper: flush remaining buffered audio
                    if state.flush_task and not state.flush_task.done():
                        state.flush_task.cancel()
                    state.flush_task = None
                    await _flush_audio_buffer(state, ws, connection_id)

            elif action == "summarize":
                mid = data.get("meetingId") or state.meeting_id
                inline_messages = data.get("messages")
                logger.warning("[ws] summarize received: meetingId=%s inline_count=%s", mid, len(inline_messages) if inline_messages else 0)
                if mid:
                    asyncio.create_task(_stream_summary(ws, mid, state.model_id, inline_messages=inline_messages))

            elif action == "ttsRequest":
                msg_id = data.get("messageId") or generate_message_id()
                tts_text = (data.get("text") or "").strip()
                model_id = data.get("modelId") or state.model_id
                polly_engine = data.get("pollyEngine") or "generative"
                polly_voice_id = data.get("pollyVoiceId") or "Ruth"
                if tts_text:
                    asyncio.create_task(
                        _handle_tts_request(
                            ws, msg_id, tts_text, model_id,
                            polly_engine, polly_voice_id, _iso_now(),
                            meeting_id=state.meeting_id or "",
                        )
                    )

            elif action == "translateMessage":
                msg_id = data.get("messageId")
                orig_text = (data.get("originalText") or "").strip()
                speaker = data.get("speaker") or state.speaker
                source_lang = normalize_language(
                    data.get("sourceLang") or state.source_lang or "en"
                )
                target_lang = data.get("targetLang") or state.target_lang or "ko"
                model_id = data.get("modelId") or state.model_id
                if msg_id and orig_text:
                    asyncio.create_task(
                        _retranslate_segment(
                            ws, msg_id, orig_text, speaker,
                            source_lang, target_lang, model_id, _iso_now(),
                        )
                    )

            elif action == "sendAudio":
                audio_data: Optional[str] = data.get("audioData")
                if audio_data:
                    audio_bytes = base64.b64decode(audio_data)

                    if state.stt_provider == "transcribe":
                        # Streaming: feed PCM directly into Transcribe session
                        pcm = audio_bytes[44:]          # strip 44-byte WAV header
                        if len(pcm) >= 3200:            # skip chunks < ~100ms
                            # transcribe_task가 예기치 않게 종료됐으면 자동 재시작
                            if state.transcribe_task and state.transcribe_task.done():
                                logger.warning(
                                    "[ws] transcribe_task died unexpectedly, restarting: connection_id=%s",
                                    connection_id,
                                )
                                state.audio_queue = asyncio.Queue()
                                state.seg_buffer.clear()
                                state.seg_last_speaker = None
                                if state.seg_flush_task and not state.seg_flush_task.done():
                                    state.seg_flush_task.cancel()
                                    state.seg_flush_task = None
                                state.transcribe_task = asyncio.create_task(
                                    _run_transcribe_streaming(state, ws, connection_id)
                                )
                            await state.audio_queue.put(pcm)
                    else:
                        # Whisper: silence-gated buffer/flush
                        energy = compute_rms(audio_bytes)
                        if energy >= SILENCE_RMS_THRESHOLD:
                            state.audio_buffer.append(audio_bytes)
                            total_size = sum(len(c) for c in state.audio_buffer)
                            if total_size > MAX_BUFFER_BYTES:
                                await _flush_audio_buffer(state, ws, connection_id)
                            else:
                                await _schedule_flush(state, ws, connection_id)
                        elif state.audio_buffer:
                            await _schedule_flush(state, ws, connection_id)
                        # else: silent + empty buffer → ignore

    except WebSocketDisconnect:
        logger.info("[ws] Disconnected: connection_id=%s", connection_id)
    finally:
        # Clean up both modes
        if state.transcribe_task and not state.transcribe_task.done():
            state.transcribe_task.cancel()
        if state.flush_task and not state.flush_task.done():
            state.flush_task.cancel()
        if state.seg_flush_task and not state.seg_flush_task.done():
            state.seg_flush_task.cancel()
        active_connections.pop(connection_id, None)
        _cleanup_ws_lock(ws)
