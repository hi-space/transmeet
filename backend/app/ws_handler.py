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
  0. { type: "subtitle_stream", phase: "stt_partial", originalText, detectedLanguage }
     (live, not committed)

Translation direction:
  - sourceLang "ko"/"en" → targetLang is fixed for the whole recording.
  - sourceLang "auto"    → targetLang is derived per utterance from the detected
                           language (ko→en, en→ko); the client's targetLang is ignored.
                           Speaker diarization is unavailable in this mode
                           (identify_language cannot be combined with speaker labels,
                           and ko-KR does not support streaming diarization at all).

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
import re
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.model import TranscriptEvent as TranscribeEvent
from botocore.exceptions import ClientError
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .aws_clients import (
    SUMMARY_CLIENT_CONFIG,
    SILENCE_RMS_THRESHOLD,
    bedrock_client,
    compute_rms,
    dynamodb_client,
    generate_message_id,
    is_hallucination,
    normalize_language,
    polly_client,
    resolve_detected_language,
    sagemaker_client,
)
from . import audio_store, mailer
from .auth import AuthError, owns_meeting, verify_id_token_async
from .config import config
from .page_agent import (
    PageRequestError,
    check_meeting_id,
    is_transient_agent_error,
    page_status_event,
    run_unpublish,
    start_page_generation,
)
from .summary_prompt import SUMMARY_SYSTEM_PROMPT, build_summary_prompt, transcript_lines

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_BUFFER_BYTES = 512_000   # ~8 seconds at 16 kHz 16-bit mono
FLUSH_DELAY_SECS = 0.6       # 600 ms silence → flush (Whisper mode only)
SEGMENT_MERGE_DELAY_SECS = 2.0  # Transcribe: wait this long before translating (accumulates consecutive finals)

# Transcribe 세션 시작이 계속 실패할 때 재시작 폭주를 막는다.
# sendAudio 는 700ms 마다 오므로 가드가 없으면 초당 1회 세션 생성 + 트레이스백 로깅이
# 무한 반복되어 0.5 vCPU 태스크를 태우고, 같은 태스크를 쓰는 다른 연결까지 느려진다.
TRANSCRIBE_MAX_START_FAILURES = 3
TRANSCRIBE_RETRY_COOLDOWN_SECS = 2.0

# 녹음 오디오를 S3 구간으로 올리는 크기. 16 kHz × 2 바이트 × 60초 — 예기치 않게 죽어도 잃는 건 1분 이내.
AUDIO_PART_BYTES = 1_920_000
# 다운로드 버튼이 받는 서명 URL. 누르자마자 쓰므로 짧게 둔다.
DOWNLOAD_URL_EXPIRES = 300

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


def _is_auto(source_lang: Optional[str]) -> bool:
    """sourceLang 이 자동 감지 모드인지."""
    return source_lang in ("auto", "", None)


def _auto_target_lang(detected_language: str) -> str:
    """AUTO 방향: 감지된 언어의 반대편으로 번역한다."""
    return "en" if detected_language == "ko" else "ko"


@dataclass
class ConnectionState:
    ws: WebSocket
    # 연결 시 검증한 Cognito sub. meeting_id 는 이 사용자가 소유한 미팅만 들어간다 (_claim_meeting)
    user_id: str = ""
    meeting_id: Optional[str] = None
    owned_meetings: set = field(default_factory=set)
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
    # 세션 시작 연속 실패 횟수 / 다음 재시도 허용 시각 (event-loop time)
    transcribe_fail_count: int = 0
    transcribe_retry_at: float = 0.0
    # Transcribe mode: deferred segment accumulation (merges consecutive finals before translating)
    seg_buffer: List[tuple] = field(default_factory=list)  # [(text, speaker, detected_lang)]
    seg_flush_task: Optional[asyncio.Task] = None
    seg_last_speaker: Optional[str] = None  # 버퍼에 있는 마지막 화자 (화자 변경 감지용)
    # Translation timing: "sentence" | "realtime" | "manual"
    translation_timing: str = "sentence"
    # 미팅 페이지 생성 태스크 — 연결당 동시에 하나만
    page_task: Optional[asyncio.Task] = None
    # ID 토큰의 email 클레임(검증된 주소만). 미팅 종료 메일 수신자.
    user_email: str = ""
    # 아직 S3 에 올리지 않은 녹음 PCM 과 그 PCM 이 속한 미팅. 미팅을 바꾸면 이전 미팅 몫을 먼저 올린다.
    audio_pcm: bytearray = field(default_factory=bytearray)
    audio_meeting_id: Optional[str] = None


# In-memory connection registry (single-process; scales with one Fargate task)
active_connections: Dict[str, ConnectionState] = {}


_ws_send_locks: Dict[int, asyncio.Lock] = {}

# 미팅별로 진행 중인 구간 업로드. 합치기 전에 기다려야 방금 올린 구간이 빠지지 않는다.
_pending_uploads: Dict[str, set] = {}


# ─── 녹음 오디오 보관 ─────────────────────────────────────────────────────────

def _start_flush(state: "ConnectionState", conn_id: str) -> None:
    """버퍼를 비우고 업로드를 백그라운드로 건다 — 녹음·번역 경로를 막지 않는다."""
    meeting_id = state.audio_meeting_id
    if not meeting_id or not state.audio_pcm:
        return
    pcm = bytes(state.audio_pcm)
    state.audio_pcm.clear()

    async def _upload() -> None:
        try:
            await audio_store.upload_part(meeting_id, conn_id, pcm)
        except Exception:
            logger.exception("[audio] part upload failed meeting_id=%s bytes=%d", meeting_id, len(pcm))

    task = asyncio.create_task(_upload())
    pending = _pending_uploads.setdefault(meeting_id, set())
    pending.add(task)
    task.add_done_callback(pending.discard)


def _buffer_audio(state: "ConnectionState", conn_id: str, pcm: bytes) -> None:
    """소유가 확인된 미팅의 녹음만 모은다 (발언 저장과 같은 규칙)."""
    if not state.meeting_id or not pcm:
        return
    if state.audio_meeting_id != state.meeting_id:
        _start_flush(state, conn_id)
        state.audio_meeting_id = state.meeting_id
    state.audio_pcm.extend(pcm)
    if len(state.audio_pcm) >= AUDIO_PART_BYTES:
        _start_flush(state, conn_id)


async def _await_uploads(meeting_id: str) -> None:
    tasks = list(_pending_uploads.get(meeting_id, ()))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _flush_meeting_audio(meeting_id: str) -> None:
    """이 미팅을 녹음 중인 모든 연결의 버퍼를 올리고 끝날 때까지 기다린다."""
    for conn_id, st in list(active_connections.items()):
        if st.audio_meeting_id == meeting_id:
            _start_flush(st, conn_id)
    await _await_uploads(meeting_id)


async def flush_all_audio() -> None:
    """프로세스 종료(SIGTERM) 때 main.py lifespan 이 부른다. ECS 는 기본 30초를 기다려 준다."""
    for conn_id, st in list(active_connections.items()):
        _start_flush(st, conn_id)
    tasks = [t for pending in _pending_uploads.values() for t in pending]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _meeting_broadcaster(meeting_id: str):
    """페이지 생성·요약 이벤트를 그 미팅을 보고 있는 *현재* 연결 전부에 보낸다.

    생성은 수 분 걸리고 그동안 브라우저 소켓은 바뀔 수 있다. 요청한 소켓 하나에 묶으면
    재접속한 클라이언트는 결과를 받지 못한다. 연결이 하나도 없으면 조용히 버린다 —
    종료 이벤트는 page_agent 가 기억하고 있어 page_status 로 되찾을 수 있다.
    """
    async def _send(payload: dict) -> None:
        targets = [s.ws for s in list(active_connections.values()) if s.meeting_id == meeting_id]
        for target in targets:
            try:
                await _safe_send(target, payload)
            except Exception:
                logger.debug("[ws] broadcast to a closed socket skipped meeting=%s", meeting_id, exc_info=True)
    return _send


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

    WS 송신 실패가 DDB 저장을 막지 않는다 — 페이지 새로고침/연결 끊김 도중에도
    이미 트랜스크립트가 만들어진 발화는 반드시 영구 저장되어야 한다.
    """
    message_id = generate_message_id()
    meeting_id = state.meeting_id
    bedrock_model_id = state.model_id

    # AUTO 모드에서는 방향이 녹음 시작 시점 설정이 아니라 발화 단위 감지 결과로
    # 결정된다 — 고정 target 을 쓰면 한국어가 들어왔을 때 ko→ko 가 된다.
    if _is_auto(state.source_lang):
        translation_target = _auto_target_lang(detected_language)
    else:
        translation_target = state.target_lang or "ko"
    logger.info(
        "[process_segment] speaker=%s detectedLang=%s auto=%s -> translation_target=%s text=%r",
        speaker, detected_language, _is_auto(state.source_lang),
        translation_target, original_text,
    )
    source_lang_label = "Korean" if detected_language == "ko" else "English"
    target_lang_label = "Korean" if translation_target == "ko" else "English"

    # ── 1: Push STT result immediately ─────────────────────────────────────────
    try:
        await _safe_send(ws, {
            "type": "subtitle_stream",
            "messageId": message_id,
            "phase": "stt",
            "speaker": speaker,
            "originalText": original_text,
            "timestamp": timestamp,
        })
    except Exception:
        logger.warning("[process_segment] WS send (stt) failed for message_id=%s", message_id)

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
                try:
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
                    # WS may be closing — keep accumulating translation for DB save
                    pass
    except Exception:
        logger.exception("[process_segment] Bedrock translation failed for message_id=%s", message_id)
        # 번역 실패해도 done phase는 보내야 프론트엔드 말풍선이 stuck 안 됨

    # ── 3: Push final subtitle ─────────────────────────────────────────────────
    try:
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
    except Exception:
        logger.warning("[process_segment] WS send (done) failed for message_id=%s", message_id)

    # ── 4: Persist to DynamoDB ─────────────────────────────────────────────────
    if meeting_id:
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
                    # 삭제되었거나 존재하지 않는 회의에 append 하면 createdAt/title 이 없는
                    # 유령 레코드가 생겨 목록 정렬을 망가뜨린다.
                    ConditionExpression="attribute_exists(meetingId)",
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
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                logger.warning(
                    "[process_segment] Skipped save — meeting does not exist: meeting_id=%s",
                    meeting_id,
                )
            else:
                logger.exception(
                    "[process_segment] DynamoDB save failed for message_id=%s meeting_id=%s",
                    message_id, meeting_id,
                )
        except Exception:
            logger.exception(
                "[process_segment] DynamoDB save failed for message_id=%s meeting_id=%s",
                message_id, meeting_id,
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
    if _is_auto(lang_code):
        # language_code 는 amazon-transcribe 0.6.4 에서 default 가 없는 필수 인자다.
        # 자동 식별 모드에서도 반드시 넘겨야 하며, None 이면 직렬화 단계에서
        # x-amzn-transcribe-language-code 헤더가 빠진다 (serialize._serialize_header).
        kwargs["language_code"] = None
        kwargs["identify_language"] = True
        # 반드시 list — SDK 가 ",".join(value) 를 하므로 문자열을 넘기면
        # "e,n,-,U,S,,,k,o,-,K,R" 로 망가지고 AWS 가 BadRequestException 을 준다.
        kwargs["language_options"] = ["en-US", "ko-KR"]
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
        state.transcribe_fail_count = 0
    except Exception:
        state.transcribe_fail_count += 1
        state.transcribe_retry_at = (
            asyncio.get_running_loop().time() + TRANSCRIBE_RETRY_COOLDOWN_SECS
        )
        logger.exception(
            "[Transcribe] Failed to start session for connection_id=%s (consecutive failures=%d)",
            connection_id, state.transcribe_fail_count,
        )
        # 조용히 죽으면 사용자는 마이크가 고장난 줄 안다 — 포기 시점에 명시적으로 알린다.
        if state.transcribe_fail_count >= TRANSCRIBE_MAX_START_FAILURES:
            try:
                await _safe_send(ws, {
                    "type": "error",
                    "message": "음성 인식 세션을 시작할 수 없습니다. 녹음을 멈추고 다시 시도해 주세요.",
                    "timestamp": _iso_now(),
                })
            except Exception:
                pass
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

                    # partial 번역도 방향을 알아야 하므로 partial/final 모두 언어를 해석한다.
                    if _is_auto(state.source_lang):
                        language = resolve_detected_language(detected_lang, transcript_text)
                    else:
                        language = normalize_language(state.source_lang)

                    if result.is_partial:
                        # Live partial → pending bubble on frontend (not committed)
                        # last_confirmed_speaker: 마지막 final에서 확인된 화자 사용
                        # (첫 발화 전엔 default "speaker1", 이후엔 실제 화자 반영)
                        # detectedLanguage: AUTO 모드에서 프론트가 partial 번역 방향을
                        # 추측하지 않도록 함께 보낸다.
                        try:
                            await _safe_send(ws, {
                                "type": "subtitle_stream",
                                "phase": "stt_partial",
                                "messageId": "partial",
                                "speaker": state.last_confirmed_speaker,
                                "originalText": transcript_text,
                                "detectedLanguage": language,
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

                        # language 는 위에서 partial/final 공용으로 해석해 둔 값이다.
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
        detected_language = resolve_detected_language(detected_lang_raw, original_text)

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
                    ConditionExpression="attribute_exists(meetingId)",
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
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                logger.warning(
                    "[tts_request] Skipped save — meeting does not exist: meeting_id=%s",
                    meeting_id,
                )
            else:
                logger.exception("[tts_request] DynamoDB save failed for messageId=%s", message_id)
        except Exception:
            logger.exception("[tts_request] DynamoDB save failed for messageId=%s", message_id)


# ─── Summary streaming ───────────────────────────────────────────────────────

_DEFAULT_TITLE_PREFIX = "Meeting "


async def _generate_title_if_default(
    meeting_id: str,
    current_title: str,
    transcript_lines: List[str],
    model_id: str,
) -> Optional[str]:
    """Generate and persist an AI title only when the current title is the auto-default.

    Returns the new title on success, None if skipped or failed.
    """
    if not current_title.startswith(_DEFAULT_TITLE_PREFIX):
        return None
    if not transcript_lines:
        return None
    try:
        head = "\n".join(transcript_lines[:20])
        prompt = (
            "다음 회의 내용을 보고 짧은 제목을 생성하세요 (10-20자, 한국어). "
            f"제목만 출력하세요.\n\n{head}"
        )
        async with bedrock_client() as br:
            resp = await br.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 64},
            )
        title = (resp.get("output", {}).get("message", {}).get("content", [{}])[0].get("text", "") or "").strip()
        if not title:
            return None
        async with dynamodb_client() as ddb:
            await ddb.update_item(
                TableName=config.MEETINGS_TABLE,
                Key={"meetingId": {"S": meeting_id}},
                UpdateExpression="SET title = :t, #updatedAt = :u",
                ExpressionAttributeNames={"#updatedAt": "updatedAt"},
                ExpressionAttributeValues={
                    ":t": {"S": title},
                    ":u": {"S": _iso_now()},
                },
            )
        return title
    except Exception:
        logger.exception("[title] auto-generate failed for meeting_id=%s", meeting_id)
        return None


# 같은 모델 재시도 사이의 대기(초). 요약은 사용자가 스피너를 보며 기다리므로 페이지 에이전트(5, 15)보다 짧다.
SUMMARY_RETRY_BACKOFF_S: tuple = (2.0, 5.0)


class SummaryModelUnavailable(Exception):
    """기본·대체 모델이 모두 일시 거절(ServiceUnavailable/Throttling)을 반복했다."""


async def _stream_summary(
    send,
    meeting_id: str,
    model_id: str,
    *,
    inline_messages: Optional[list] = None,
) -> Optional[str]:
    """Fetch meeting messages and stream a summary via converse_stream. 만든 요약을 돌려주고, 실패하면 None.

    If inline_messages is provided (from frontend), use those directly
    instead of reading from DynamoDB. This ensures summaries work during
    live recording when messages may not yet be persisted.

    `model_id` 는 호출자의 실시간 모델이지만 요약은 `config.SUMMARY_MODEL_ID` 로 쓴다.

    `send` 는 그 미팅의 *현재* 연결 전부에 보내는 함수(`_meeting_broadcaster`)다. Opus 5.5 는 첫 토큰까지
    수 초~수십 초 침묵하므로, 요청한 소켓 하나에 묶으면 그 사이 미팅 전환·재접속으로 소켓이 바뀔 때
    닫힌 소켓에 보내다 예외로 죽고 저장도 안 된다(2026-10-09). 보낼 곳이 없어도 요약은 끝까지 만들어 저장한다.
    """
    try:
        lines: List[str] = []
        current_title = ""
        if inline_messages:
            lines = transcript_lines(inline_messages)
            async with dynamodb_client() as ddb:
                meta = await ddb.get_item(
                    TableName=config.MEETINGS_TABLE,
                    Key={"meetingId": {"S": meeting_id}},
                    ProjectionExpression="title",
                )
            current_title = meta.get("Item", {}).get("title", {}).get("S", "")
        else:
            async with dynamodb_client() as ddb:
                result = await ddb.get_item(
                    TableName=config.MEETINGS_TABLE,
                    Key={"meetingId": {"S": meeting_id}},
                )
            current_title = result.get("Item", {}).get("title", {}).get("S", "")
            raw_messages = result.get("Item", {}).get("messages", {}).get("L", [])
            lines = transcript_lines(
                {"originalText": m.get("M", {}).get("originalText", {}).get("S", ""),
                 "timestamp": m.get("M", {}).get("timestamp", {}).get("S", "")}
                for m in raw_messages
            )

        if not lines:
            await send({"type": "summary_stream", "phase": "error", "meetingId": meeting_id, "error": "No messages to summarize"})
            return None
        transcript = "\n".join(lines)

        user_prompt = build_summary_prompt(current_title, transcript)
        summary_model_id = config.SUMMARY_MODEL_ID or model_id
        logger.info("[ws] summary start meeting_id=%s model=%s lines=%d", meeting_id, summary_model_id, len(lines))

        summary = ""
        async with bedrock_client(SUMMARY_CLIENT_CONFIG) as br:
            # Bedrock 이 일시적으로 거절(ServiceUnavailable/Throttling)하면 같은 모델로 재시도하고, 그래도 안 되면
            # 대체 모델로 넘어간다(페이지 에이전트와 같은 정책). 이미 델타를 보낸 뒤 끊긴 스트림은 클라이언트가
            # 되돌릴 수 없어 재시도하지 않는다.
            failures = 0
            using_fallback = False
            while True:
                try:
                    stream_resp = await br.converse_stream(
                        modelId=summary_model_id,
                        system=[{"text": SUMMARY_SYSTEM_PROMPT}],
                        messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                        inferenceConfig={"maxTokens": config.SUMMARY_MAX_TOKENS},
                        additionalModelRequestFields={"output_config": {"effort": config.SUMMARY_EFFORT}},
                    )
                    async for event in stream_resp["stream"]:
                        delta = event.get("contentBlockDelta", {}).get("delta", {}).get("text")
                        if not delta:
                            continue
                        summary += delta
                        await send({"type": "summary_stream", "phase": "delta", "meetingId": meeting_id, "text": delta})
                    break
                except Exception as e:
                    if summary or not is_transient_agent_error(e):
                        raise
                    failures += 1
                    if failures > config.SUMMARY_INVOKE_RETRIES:
                        fallback = config.SUMMARY_FALLBACK_MODEL_ID
                        if using_fallback or not fallback or fallback == summary_model_id:
                            raise SummaryModelUnavailable(str(e)) from e
                        logger.warning("[ws] summary primary model exhausted meeting_id=%s, switching to %s: %s",
                                       meeting_id, fallback, e)
                        summary_model_id = fallback
                        using_fallback = True
                        failures = 0
                        continue
                    delay = SUMMARY_RETRY_BACKOFF_S[min(failures - 1, len(SUMMARY_RETRY_BACKOFF_S) - 1)]
                    logger.warning("[ws] summary transient failure meeting_id=%s model=%s attempt=%d/%d: %s",
                                   meeting_id, summary_model_id, failures, config.SUMMARY_INVOKE_RETRIES, e)
                    if delay:
                        await asyncio.sleep(delay)

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

        new_title = await _generate_title_if_default(meeting_id, current_title, lines, model_id)

        done_payload: Dict[str, Any] = {"type": "summary_stream", "phase": "done", "meetingId": meeting_id, "summary": summary}
        if new_title:
            done_payload["title"] = new_title
        await send(done_payload)
        logger.info("[ws] summary done meeting_id=%s chars=%d", meeting_id, len(summary))
        return summary

    except SummaryModelUnavailable as e:
        logger.error("[ws] _stream_summary models unavailable for meeting_id=%s: %s", meeting_id, e)
        await send({"type": "summary_stream", "phase": "error", "meetingId": meeting_id,
                    "error": "모델이 일시적으로 응답하지 않습니다. 잠시 후 다시 시도해 주세요"})
    except Exception:
        logger.exception("[ws] _stream_summary failed for meeting_id=%s", meeting_id)
        await send({"type": "summary_stream", "phase": "error", "meetingId": meeting_id, "error": "Summary generation failed"})
    return None


# ─── 녹음 다운로드 · 미팅 종료 ────────────────────────────────────────────────

async def _meeting_meta(meeting_id: str) -> tuple[str, str]:
    """(제목, 생성 시각)."""
    async with dynamodb_client() as ddb:
        result = await ddb.get_item(
            TableName=config.MEETINGS_TABLE,
            Key={"meetingId": {"S": meeting_id}},
            ProjectionExpression="title, createdAt",
        )
    item = result.get("Item", {})
    return item.get("title", {}).get("S", ""), item.get("createdAt", {}).get("S", "")


def _audio_filename(title: str) -> str:
    # 파일 이름에 못 쓰는 문자만 바꾼다. 한글 제목은 그대로 둔다.
    safe = re.sub(r'[\\/:*?"<>|\r\n]+', "_", title or "").strip() or "meeting"
    return f"{safe[:80]}.mp3"


async def _merged_audio_url(meeting_id: str, title: str, expires: int) -> Optional[str]:
    """그 순간까지의 녹음을 합친 MP3 의 서명 URL. 녹음이 없거나 합치기에 실패하면 None."""
    await _flush_meeting_audio(meeting_id)
    try:
        key = await audio_store.ensure_merged(meeting_id)
        if not key:
            return None
        return await audio_store.presign(key, expires, _audio_filename(title))
    except Exception:
        logger.exception("[audio] merge/presign failed meeting_id=%s", meeting_id)
        return None


async def _prepare_audio(send, meeting_id: str) -> None:
    title, _ = await _meeting_meta(meeting_id)
    url = await _merged_audio_url(meeting_id, title, DOWNLOAD_URL_EXPIRES)
    if url:
        await send({"type": "audio_ready", "meetingId": meeting_id, "url": url})
    else:
        await send({"type": "audio_ready", "meetingId": meeting_id, "error": "저장된 녹음이 없습니다"})


async def _claim_email_send(meeting_id: str) -> bool:
    """메일 '발송권'. 종료 연타·여러 연결에서 동시에 종료해도 한 쪽만 True 를 받는다."""
    async with dynamodb_client() as ddb:
        try:
            await ddb.update_item(
                TableName=config.MEETINGS_TABLE,
                Key={"meetingId": {"S": meeting_id}},
                UpdateExpression="SET emailSentAt = :t",
                ConditionExpression="attribute_not_exists(emailSentAt)",
                ExpressionAttributeValues={":t": {"S": _iso_now()}},
            )
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise
    return True


async def _release_email_send(meeting_id: str) -> None:
    """발송이 실패하면 발송권을 돌려놓아 다음 종료 때 다시 보낼 수 있게 한다."""
    async with dynamodb_client() as ddb:
        await ddb.update_item(
            TableName=config.MEETINGS_TABLE,
            Key={"meetingId": {"S": meeting_id}},
            UpdateExpression="REMOVE emailSentAt",
        )


async def _send_end_email(state: ConnectionState, meeting_id: str, summary: Optional[str]) -> str:
    """메일 단계의 결과 상태: sent | already_sent | unverified | no_email | failed."""
    if not state.user_email:
        return "no_email"
    if not await mailer.email_verified(state.user_email):
        return "unverified"
    if not await _claim_email_send(meeting_id):
        return "already_sent"
    try:
        title, created_at = await _meeting_meta(meeting_id)
        audio_url = await _merged_audio_url(meeting_id, title, mailer.EMAIL_AUDIO_URL_EXPIRES)
        mail = mailer.build_meeting_email(
            title=title,
            started_at=created_at,
            summary=summary,
            audio_url=audio_url,
            meeting_url=f"{config.APP_BASE_URL}/?meeting={meeting_id}",
        )
        await mailer.send_meeting_email(state.user_email, mail)
    except Exception:
        logger.exception("[mail] send failed meeting_id=%s", meeting_id)
        await _release_email_send(meeting_id)
        return "failed"
    logger.info("[mail] sent meeting_id=%s", meeting_id)
    return "sent"


async def _end_meeting(send, state: ConnectionState, meeting_id: str, *, email_on_end: bool) -> None:
    """오디오 마무리 → 요약 → 메일. `send` 는 미팅 브로드캐스터라 소켓이 바뀌어도 끝까지 간다."""
    try:
        await send({"type": "meeting_end", "meetingId": meeting_id, "phase": "audio"})
        # 메일을 안 보내도 합쳐 둔다 — 다운로드 버튼이 기다리지 않게
        title, _ = await _meeting_meta(meeting_id)
        await _merged_audio_url(meeting_id, title, DOWNLOAD_URL_EXPIRES)
        async with dynamodb_client() as ddb:
            await ddb.update_item(
                TableName=config.MEETINGS_TABLE,
                Key={"meetingId": {"S": meeting_id}},
                UpdateExpression="SET endedAt = :t",
                ExpressionAttributeValues={":t": {"S": _iso_now()}},
            )

        await send({"type": "meeting_end", "meetingId": meeting_id, "phase": "summary"})
        summary = await _stream_summary(send, meeting_id, state.model_id)

        status = await _send_end_email(state, meeting_id, summary) if email_on_end else "skipped"
        await send({"type": "meeting_end", "meetingId": meeting_id, "phase": "email", "status": status})
        await send({"type": "meeting_end", "meetingId": meeting_id, "phase": "done"})
    except Exception:
        logger.exception("[ws] end meeting failed meeting_id=%s", meeting_id)
        await send({"type": "meeting_end", "meetingId": meeting_id, "phase": "error",
                    "error": "미팅 종료 처리에 실패했습니다. 다시 시도해 주세요."})


# ─── Q&A streaming ─────────────────────────────────────────────────────────

async def _stream_qa_answer(
    ws: WebSocket,
    message_id: str,
    question: str,
    model_id: str,
    *,
    inline_messages: Optional[list] = None,
) -> None:
    try:
        lines: List[str] = []
        if inline_messages:
            for m in inline_messages:
                speaker = m.get("speaker", "")
                orig = m.get("original", "")
                if orig:
                    lines.append(f"[{speaker}] {orig}")

        if not lines:
            await _safe_send(ws, {"type": "qa_stream", "messageId": message_id, "phase": "error", "error": "No meeting context"})
            return
        transcript = "\n".join(lines)

        system_prompt = (
            "당신은 회의 어시스턴트입니다. 아래 회의 내용만을 기반으로 질문에 답변하세요.\n"
            "회의 내용에 없는 정보는 \"회의에서 언급되지 않았습니다\"라고 답변하세요.\n"
            "외부 지식을 사용하지 마세요. 답변은 한국어로 간결하게 작성하세요."
        )
        user_prompt = f"[회의 내용]\n{transcript}\n\n[질문]\n{question}"

        answer = ""
        async with bedrock_client() as br:
            stream_resp = await br.converse_stream(
                modelId=model_id,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                inferenceConfig={"maxTokens": 2048},
            )
            async for event in stream_resp["stream"]:
                delta = event.get("contentBlockDelta", {}).get("delta", {}).get("text")
                if not delta:
                    continue
                answer += delta
                await _safe_send(ws, {"type": "qa_stream", "messageId": message_id, "phase": "delta", "text": delta})

        await _safe_send(ws, {"type": "qa_stream", "messageId": message_id, "phase": "done", "answer": answer})

    except Exception:
        logger.exception("[ws] _stream_qa_answer failed for message_id=%s", message_id)
        try:
            await _safe_send(ws, {"type": "qa_stream", "messageId": message_id, "phase": "error", "error": "Q&A generation failed"})
        except Exception:
            pass


# ─── WebSocket endpoint ──────────────────────────────────────────────────────

# 토큰이 없거나 검증에 실패한 연결을 닫는 코드. 프런트는 이 코드면 재연결하지 않는다.
WS_CLOSE_UNAUTHORIZED = 4401
# 연결 후 첫 메시지 {action: "auth", token} 를 기다리는 시간.
# 토큰을 URL 쿼리로 받지 않는 이유: uvicorn 이 WebSocket 접속 로그에 쿼리 문자열을 그대로 남긴다.
WS_AUTH_TIMEOUT_SECS = 10.0


async def _authenticate(ws: WebSocket) -> tuple[str, str]:
    """첫 메시지의 Cognito ID 토큰을 검증해 (sub, email)을 돌려준다. 실패하면 AuthError."""
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=WS_AUTH_TIMEOUT_SECS)
    except asyncio.TimeoutError as e:
        raise AuthError("auth message timeout") from e
    except (ValueError, KeyError) as e:
        raise AuthError("malformed auth message") from e
    if not isinstance(first, dict) or first.get("action") != "auth":
        raise AuthError("first message must be auth")
    return await verify_id_token_async(first.get("token") or "")


async def _claim_meeting(state: ConnectionState, meeting_id: Optional[str]) -> Optional[str]:
    """연결 사용자가 소유한 미팅이면 그 ID 를, 아니면 None 을 돌려준다. 확인된 ID 는 연결 동안 캐시한다."""
    if not meeting_id:
        return None
    if meeting_id in state.owned_meetings:
        return meeting_id
    try:
        owned = await owns_meeting(state.user_id, meeting_id)
    except Exception:
        logger.exception("[ws] ownership check failed meeting_id=%s", meeting_id)
        return None
    if not owned:
        logger.warning("[ws] meeting not owned by caller: meeting_id=%s user_id=%s", meeting_id, state.user_id)
        return None
    state.owned_meetings.add(meeting_id)
    return meeting_id


async def _stop_recording(state: ConnectionState, ws: WebSocket, connection_id: str) -> None:
    """녹음 정지: STT 를 마무리하고 아직 안 올린 녹음을 S3 로 올린다."""
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
    _start_flush(state, connection_id)


@router.websocket("/ws")
async def ws_endpoint(
    ws: WebSocket,
    meetingId: Optional[str] = None,
):
    # 먼저 수락해야 브라우저가 닫힘 코드(4401)를 받는다 — 수락 전에 닫으면 1006 만 보인다
    await ws.accept()
    try:
        user_id, user_email = await _authenticate(ws)
    except WebSocketDisconnect:
        return
    except AuthError as e:
        logger.warning("[ws] rejected unauthenticated connection: %s", e)
        await ws.close(code=WS_CLOSE_UNAUTHORIZED, reason="unauthorized")
        return
    connection_id = str(uuid4())
    state = ConnectionState(ws=ws, user_id=user_id, user_email=user_email)
    state.meeting_id = await _claim_meeting(state, meetingId)
    active_connections[connection_id] = state
    logger.info("[ws] Connected: connection_id=%s meetingId=%s", connection_id, meetingId)

    try:
        while True:
            data = await ws.receive_json()
            action = data.get("action")
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
                # 명시적 재시작(녹음 버튼/언어 전환)은 실패 카운터를 리셋한다 —
                # 이전 세션에서 포기했더라도 사용자가 다시 시도하면 기회를 준다.
                state.transcribe_fail_count = 0
                state.transcribe_retry_at = 0.0

                state.stt_provider = data.get("sttProvider", "whisper")
                state.target_lang = data.get("targetLang", "ko") or "ko"
                state.source_lang = data.get("sourceLang", "auto") or "auto"
                state.model_id = data.get("modelId") or config.BEDROCK_MODEL_ID
                if data.get("meetingId"):
                    # 남의 미팅이면 None — 발언은 저장되지 않는다
                    state.meeting_id = await _claim_meeting(state, data.get("meetingId"))
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
                await _stop_recording(state, ws, connection_id)

            elif action == "endMeeting":
                mid = await _claim_meeting(state, data.get("meetingId") or state.meeting_id)
                if not mid:
                    await _safe_send(ws, {"type": "meeting_end", "phase": "error", "error": "미팅이 선택되지 않았습니다"})
                else:
                    await _stop_recording(state, ws, connection_id)
                    asyncio.create_task(_end_meeting(
                        _meeting_broadcaster(mid), state, mid, email_on_end=bool(data.get("emailOnEnd")),
                    ))

            elif action == "prepare_audio":
                mid = await _claim_meeting(state, data.get("meetingId") or state.meeting_id)
                if mid:
                    async def _send_audio(payload: dict, _ws=ws) -> None:
                        await _safe_send(_ws, payload)

                    asyncio.create_task(_prepare_audio(_send_audio, mid))

            elif action == "summarize":
                mid = await _claim_meeting(state, data.get("meetingId") or state.meeting_id)
                inline_messages = data.get("messages")
                if mid:
                    # 결과는 요청한 소켓이 아니라 이 미팅을 보고 있는 현재 연결 전부에 간다 (_stream_summary 참고)
                    state.meeting_id = state.meeting_id or mid
                    asyncio.create_task(
                        _stream_summary(_meeting_broadcaster(mid), mid, state.model_id, inline_messages=inline_messages)
                    )

            elif action == "generate_page":
                mid = await _claim_meeting(state, data.get("meetingId") or state.meeting_id)
                if not mid:
                    await _safe_send(ws, {"type": "page_stream", "phase": "error", "error": "미팅이 선택되지 않았습니다"})
                elif state.page_task and not state.page_task.done():
                    await _safe_send(ws, {"type": "page_stream", "meetingId": mid, "phase": "error", "error": "이미 생성 중입니다"})
                else:
                    # 미팅 ID·메시지 상한 검증, 같은 미팅 중복 생성 거절 (다른 연결이어도)
                    try:
                        state.page_task = start_page_generation(
                            _meeting_broadcaster(mid), mid, inline_messages=data.get("messages")
                        )
                    except PageRequestError as e:
                        await _safe_send(ws, {"type": "page_stream", "meetingId": mid, "phase": "error", "error": str(e)})

            elif action == "page_status":
                # 재접속한 클라이언트가 '아직 생성 중인가?' 를 묻는다 — 놓친 done/error 를 되돌려 준다
                mid = await _claim_meeting(state, data.get("meetingId") or state.meeting_id)
                if mid:
                    await _safe_send(ws, page_status_event(mid))

            elif action == "unpublish_page":
                mid = await _claim_meeting(state, data.get("meetingId") or state.meeting_id)
                if mid:
                    try:
                        check_meeting_id(mid)
                    except PageRequestError as e:
                        await _safe_send(ws, {"type": "page_stream", "meetingId": mid, "op": "unpublish",
                                              "phase": "error", "error": str(e)})
                    else:
                        async def _send_unpub(payload: dict, _ws=ws) -> None:
                            await _safe_send(_ws, payload)

                        asyncio.create_task(run_unpublish(_send_unpub, mid))

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
                # AUTO 모드에서는 state.target_lang 이 의미가 없으므로 감지 결과에서
                # 방향을 유도한다 (normalize_language("auto") 는 "en" 이라 그대로 쓰면
                # 한국어 발화가 en→ko 로 잘못 번역된다).
                raw_source = data.get("sourceLang") or state.source_lang
                if _is_auto(raw_source):
                    source_lang = resolve_detected_language("", orig_text)
                    fallback_target = _auto_target_lang(source_lang)
                else:
                    source_lang = normalize_language(raw_source)
                    fallback_target = state.target_lang or "ko"
                target_lang = data.get("targetLang") or fallback_target
                model_id = data.get("modelId") or state.model_id
                if msg_id and orig_text:
                    asyncio.create_task(
                        _retranslate_segment(
                            ws, msg_id, orig_text, speaker,
                            source_lang, target_lang, model_id, _iso_now(),
                        )
                    )

            elif action == "qaRequest":
                msg_id = data.get("messageId") or generate_message_id()
                question = (data.get("question") or "").strip()
                model_id = data.get("modelId") or config.QA_MODEL_ID
                inline_messages = data.get("messages")
                if msg_id and question:
                    asyncio.create_task(
                        _stream_qa_answer(ws, msg_id, question, model_id, inline_messages=inline_messages)
                    )

            elif action == "sendAudio":
                audio_data: Optional[str] = data.get("audioData")
                if audio_data:
                    audio_bytes = base64.b64decode(audio_data)
                    # 브라우저가 보내는 조각은 44바이트 헤더가 붙은 16 kHz mono 16-bit WAV 다
                    _buffer_audio(state, connection_id, audio_bytes[44:])

                    if state.stt_provider == "transcribe":
                        # Streaming: feed PCM directly into Transcribe session
                        pcm = audio_bytes[44:]          # strip 44-byte WAV header
                        if len(pcm) >= 3200:            # skip chunks < ~100ms
                            # transcribe_task가 예기치 않게 종료됐으면 자동 재시작.
                            # 단, 세션 시작 자체가 계속 실패하는 상황에서는 재시작을
                            # 멈춘다 — 그대로 두면 초당 1회 폭주하며 태스크를 태운다.
                            if state.transcribe_task and state.transcribe_task.done():
                                now = asyncio.get_running_loop().time()
                                if state.transcribe_fail_count >= TRANSCRIBE_MAX_START_FAILURES:
                                    # 포기 — 사용자에겐 이미 error 를 보냈다. 오디오는 버린다.
                                    continue
                                if now < state.transcribe_retry_at:
                                    continue  # 쿨다운 중 — 오디오를 버린다
                                logger.warning(
                                    "[ws] transcribe_task died unexpectedly, restarting: "
                                    "connection_id=%s (failures=%d)",
                                    connection_id, state.transcribe_fail_count,
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
        _start_flush(state, connection_id)
        active_connections.pop(connection_id, None)
        _cleanup_ws_lock(ws)
