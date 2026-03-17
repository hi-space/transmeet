"""
WebSocket audio pipeline handler.

Both STT providers (Whisper / Transcribe) use the same buffer/flush mechanism:
  - Audio chunks are buffered per connection
  - After 600ms of silence (or when buffer is full), the complete utterance is flushed
  - Flushed audio is sent to SageMaker Whisper OR AWS Transcribe (one-shot streaming)

Message sequence per flushed utterance:
  1. { type: "subtitle_stream", phase: "stt",         originalText }
  2. { type: "subtitle_stream", phase: "translating", partialTranslation } × N tokens
  3. { type: "subtitle_stream", phase: "done",        originalText, translatedText, detectedLanguage }

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

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .aws_clients import (
    SILENCE_RMS_THRESHOLD,
    bedrock_client,
    compute_rms,
    dynamodb_client,
    generate_message_id,
    is_hallucination,
    normalize_language,
    sagemaker_client,
    transcribe_streaming_client,
)
from .config import config

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_BUFFER_BYTES = 512_000   # ~8 seconds at 16 kHz 16-bit mono
FLUSH_DELAY_SECS = 0.6       # 600 ms silence → flush buffer

# Transcribe language code mapping
LANG_CODE_MAP: dict = {
    "en": "en-US",
    "ko": "ko-KR",
}


@dataclass
class ConnectionState:
    ws: WebSocket
    meeting_id: Optional[str] = None
    stt_provider: str = "whisper"
    target_lang: str = "ko"
    source_lang: str = "auto"
    model_id: str = field(default_factory=lambda: config.BEDROCK_MODEL_ID)
    speaker: str = "speaker1"
    audio_buffer: List[bytes] = field(default_factory=list)
    flush_task: Optional[asyncio.Task] = None


# In-memory connection registry (single-process; scales with one Fargate task)
active_connections: Dict[str, ConnectionState] = {}


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


async def _transcribe_once(audio_bytes: bytes, language_code: str) -> tuple[str, str]:
    """
    One-shot Transcribe Streaming for a complete buffered utterance.

    Strips the WAV header, feeds all PCM audio into a single streaming session,
    and collects only the final (IsPartial=False) transcript segments.

    Returns (transcript, detected_language: 'en'|'ko').
    """
    pcm = audio_bytes[44:]  # Strip 44-byte WAV header
    if len(pcm) < 3200:     # < ~100ms @ 16kHz 16-bit — too short for Transcribe
        logger.debug("[Transcribe] Audio too short (%d bytes PCM), skipping", len(pcm))
        return "", "en"

    # Feed PCM in ~500ms chunks so Transcribe starts processing quickly
    CHUNK_SIZE = 16000 * 2  # 500 ms @ 16kHz 16-bit mono

    async def audio_gen():
        offset = 0
        while offset < len(pcm):
            yield {"AudioEvent": {"Payload": pcm[offset : offset + CHUNK_SIZE]}}
            offset += CHUNK_SIZE

    kwargs: dict = {
        "MediaSampleRateHertz": 16000,
        "MediaEncoding": "pcm",
        "AudioStream": audio_gen(),
    }
    if language_code in ("auto", "", None):
        # Limit to the two languages the app handles to speed up detection
        kwargs["IdentifyLanguage"] = True
        kwargs["LanguageOptions"] = "en-US,ko-KR"
    else:
        kwargs["LanguageCode"] = LANG_CODE_MAP.get(language_code, "en-US")

    transcript_parts: list[str] = []
    detected_lang = language_code if language_code not in ("auto", "", None) else "en"

    try:
        async with transcribe_streaming_client() as tc:
            resp = await tc.start_stream_transcription(**kwargs)
            async for event in resp["TranscriptResultStream"]:
                results = (
                    event.get("TranscriptEvent", {})
                    .get("Transcript", {})
                    .get("Results", [])
                )
                for r in results:
                    # Only collect finalised (non-partial) segments
                    if r.get("IsPartial", True):
                        continue
                    alts = r.get("Alternatives", [])
                    if alts:
                        t = alts[0].get("Transcript", "").strip()
                        if t:
                            transcript_parts.append(t)
                    lang_id = r.get("LanguageCode", "")
                    if lang_id:
                        detected_lang = lang_id[:2].lower()
    except Exception:
        logger.exception("[Transcribe] _transcribe_once failed")
        return "", "en"

    result = " ".join(transcript_parts).strip()
    logger.info("[Transcribe] result: %r (lang=%s)", result, detected_lang)
    return result, detected_lang


async def _process_audio_bytes(
    ws: WebSocket, audio_bytes: bytes, state: ConnectionState, connection_id: str
) -> None:
    timestamp = _iso_now()
    message_id = generate_message_id()

    meeting_id = state.meeting_id
    speaker = state.speaker
    req_source_lang: Optional[str] = None if state.source_lang == "auto" else state.source_lang
    bedrock_model_id = state.model_id

    # ── Step 1: STT ──────────────────────────────────────────────────────────────
    if state.stt_provider == "transcribe":
        # One-shot Transcribe Streaming: send the complete buffered utterance at once
        logger.info("[ws-audio] Calling Transcribe, audio_len=%d", len(audio_bytes))
        original_text, detected_lang_raw = await _transcribe_once(
            audio_bytes, state.source_lang
        )
    else:
        # SageMaker Whisper (default)
        payload: dict = {
            "audio_input": audio_bytes.hex(),
            "task": "transcribe",
            "language": "english",
            "max_new_tokens": 448,
            "num_beams": 1,
            "temperature": 0,
            "do_sample": False,
            "early_stopping": False,
            "no_repeat_ngram_size": 3,
            "num_return_sequences": 1,
            "top_p": 1,
            "top_k": 50,
            "length_penalty": 1,
        }

        async with sagemaker_client() as sm:
            whisper_resp = await sm.invoke_endpoint(
                EndpointName=config.WHISPER_ENDPOINT,
                ContentType="application/json",
                Body=json.dumps(payload),
            )
            whisper_body = await whisper_resp["Body"].read()

        whisper_result = json.loads(whisper_body.decode("utf-8"))
        raw_text = whisper_result.get("text", "")
        original_text = (" ".join(raw_text) if isinstance(raw_text, list) else raw_text).strip()
        detected_lang_raw = whisper_result.get("language", "")
        logger.info("[ws-audio] Whisper result: text=%r language=%r", original_text, detected_lang_raw)

    if not original_text:
        return

    # ── Hallucination filter ──────────────────────────────────────────────────
    if is_hallucination(original_text):
        logger.info("[ws-audio] Hallucination filtered: %r", original_text)
        return

    # ── Language detection ────────────────────────────────────────────────────
    if req_source_lang:
        detected_language = normalize_language(req_source_lang)
    else:
        detected_language = normalize_language(detected_lang_raw)

    # ── Mis-detection filter: if forced English but >30% non-ASCII chars ──────
    if req_source_lang == "en":
        non_ascii = sum(1 for c in original_text if ord(c) > 127)
        if non_ascii / max(len(original_text), 1) > 0.3:
            logger.info(
                "[ws-audio] Mis-detection filtered (forced en but %.0f%% non-ASCII): %r",
                100 * non_ascii / len(original_text),
                original_text,
            )
            return

    translation_target = state.target_lang or (
        "en" if detected_language == "ko" else "ko"
    )

    source_lang_label = "Korean" if detected_language == "ko" else "English"
    target_lang_label = "Korean" if translation_target == "ko" else "English"

    # ── Step 2: Push STT result immediately ────────────────────────────────────
    await ws.send_json({
        "type": "subtitle_stream",
        "messageId": message_id,
        "phase": "stt",
        "speaker": speaker,
        "originalText": original_text,
        "timestamp": timestamp,
    })

    # ── Step 3: Stream translation via Bedrock ─────────────────────────────────
    prompt = (
        f"Translate the following {source_lang_label} text to {target_lang_label}. "
        f"Output only the translated text, no explanations, no quotes.\n\n"
        f"Text: {original_text}"
    )

    translated_text = ""
    async with bedrock_client() as br:
        stream_resp = await br.invoke_model_with_response_stream(
            modelId=bedrock_model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            }),
        )
        async for event in stream_resp["body"]:
            chunk = event.get("chunk")
            if not chunk:
                continue
            parsed = json.loads(chunk["bytes"].decode("utf-8"))
            if (
                parsed.get("type") == "content_block_delta"
                and parsed.get("delta", {}).get("type") == "text_delta"
                and parsed["delta"].get("text")
            ):
                translated_text += parsed["delta"]["text"]
                await ws.send_json({
                    "type": "subtitle_stream",
                    "messageId": message_id,
                    "phase": "translating",
                    "speaker": speaker,
                    "originalText": original_text,
                    "partialTranslation": translated_text,
                    "timestamp": timestamp,
                })

    # ── Step 4: Push final subtitle ────────────────────────────────────────────
    await ws.send_json({
        "type": "subtitle_stream",
        "messageId": message_id,
        "phase": "done",
        "speaker": speaker,
        "originalText": original_text,
        "translatedText": translated_text,
        "detectedLanguage": detected_language,
        "timestamp": timestamp,
    })

    # ── Step 5: Persist to DynamoDB ────────────────────────────────────────────
    if meeting_id:
        async with dynamodb_client() as ddb:
            await ddb.update_item(
                TableName=config.MEETINGS_TABLE,
                Key={"meetingId": {"S": meeting_id}},
                UpdateExpression=(
                    "SET messages = list_append(if_not_exists(messages, :empty), :msg), "
                    "#updatedAt = :ts"
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
                },
            )


async def _flush_audio_buffer(
    state: ConnectionState, ws: WebSocket, connection_id: str
) -> None:
    """Flush accumulated audio buffer to STT as a single utterance."""
    if not state.audio_buffer:
        return
    chunks = state.audio_buffer[:]
    state.audio_buffer.clear()
    combined_wav = combine_wav_chunks(chunks)
    try:
        await _process_audio_bytes(ws, combined_wav, state, connection_id)
    except Exception:
        logger.exception("[ws] Audio buffer flush failed for connection_id=%s", connection_id)
        await ws.send_json({
            "type": "error",
            "message": "Processing failed. Please try again.",
            "timestamp": _iso_now(),
        })


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

            if action == "ping":
                await ws.send_json({"type": "pong", "timestamp": _iso_now()})

            elif action == "startRecording":
                # Cancel any pending flush and reset buffer for fresh session
                if state.flush_task and not state.flush_task.done():
                    state.flush_task.cancel()
                state.flush_task = None
                state.audio_buffer.clear()

                state.stt_provider = data.get("sttProvider", "whisper")
                state.target_lang = data.get("targetLang", "ko") or "ko"
                state.source_lang = data.get("sourceLang", "auto") or "auto"
                state.model_id = data.get("modelId") or config.BEDROCK_MODEL_ID
                state.meeting_id = data.get("meetingId") or state.meeting_id
                state.speaker = data.get("speaker", "speaker1")
                logger.info(
                    "[ws] startRecording: provider=%s sourceLang=%s targetLang=%s",
                    state.stt_provider, state.source_lang, state.target_lang,
                )

            elif action == "stopRecording":
                # Flush remaining buffered audio before stopping
                if state.flush_task and not state.flush_task.done():
                    state.flush_task.cancel()
                state.flush_task = None
                await _flush_audio_buffer(state, ws, connection_id)

            elif action == "sendAudio":
                audio_data: Optional[str] = data.get("audioData")
                if audio_data:
                    audio_bytes = base64.b64decode(audio_data)
                    energy = compute_rms(audio_bytes)
                    if energy >= SILENCE_RMS_THRESHOLD:
                        # Voiced chunk: buffer and reschedule flush timer
                        state.audio_buffer.append(audio_bytes)
                        total_size = sum(len(c) for c in state.audio_buffer)
                        if total_size > MAX_BUFFER_BYTES:
                            # Buffer full: flush immediately
                            await _flush_audio_buffer(state, ws, connection_id)
                        else:
                            await _schedule_flush(state, ws, connection_id)
                    elif state.audio_buffer:
                        # Silent chunk but buffer has content: keep flush timer going
                        await _schedule_flush(state, ws, connection_id)
                    # else: silent + empty buffer → ignore

    except WebSocketDisconnect:
        logger.info("[ws] Disconnected: connection_id=%s", connection_id)
    finally:
        if state.flush_task and not state.flush_task.done():
            state.flush_task.cancel()
        active_connections.pop(connection_id, None)
