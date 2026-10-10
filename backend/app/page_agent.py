"""Meeting page generation via the LangGraph agent on Amazon Bedrock AgentCore Runtime.

Flow: build payload from DynamoDB → InvokeAgentRuntime (SSE stream) → relay the agent's
`status/delta/done/error` events to the WebSocket as `page_stream` → remember the terminal
event for `page_status`. The agent publishes the page itself (S3 + DynamoDB); this module
only retries transient failures (new session, then the fallback model) and handles unpublish.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional
from uuid import uuid4

from boto3.dynamodb.types import TypeDeserializer

from . import aws_clients
from .config import config

logger = logging.getLogger(__name__)

SPEAKER_LABELS = {"speaker1": "Speaker 1", "speaker2": "Speaker 2", "me": "Me"}
# 프런트 mergeLoadedMessages 와 동일: 10초 이내 같은 화자 연속 발언은 한 턴으로 본다.
MERGE_WINDOW_SECONDS = 10

# 에이전트 쪽 agent/runtime/model.py ALLOWED_MODEL_IDS 와 같아야 한다 (페이지 meta 에 기록되고 Runtime 이 검증한다)
PAGE_AGENT_MODEL_ID = "global.anthropic.claude-opus-5-5"
PAGE_AGENT_FALLBACK_MODEL_ID = "global.anthropic.claude-opus-5"


def _parse_ts(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        return None


def _normalize(raw: Dict[str, Any], fallback_ts: str) -> Dict[str, Any]:
    """Accept either DynamoDB-shaped (originalText) or frontend-shaped (original) messages."""
    return {
        "timestamp": raw.get("timestamp") or fallback_ts,
        "speaker": raw.get("speaker") or "speaker1",
        "original": (raw.get("originalText") or raw.get("original") or "").strip(),
        "translation": (raw.get("translatedText") or raw.get("translation") or "").strip(),
        "detectedLanguage": raw.get("detectedLanguage") or "",
    }


def merge_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    last_ts = ""
    for raw in messages:
        m = _normalize(raw, last_ts)
        if not m["original"]:
            continue
        normalized.append(m)
        last_ts = m["timestamp"]
    normalized.sort(key=lambda m: _parse_ts(m["timestamp"]) or datetime.min.replace(tzinfo=timezone.utc))

    merged: List[Dict[str, Any]] = []
    for m in normalized:
        if merged:
            prev = merged[-1]
            a, b = _parse_ts(prev["timestamp"]), _parse_ts(m["timestamp"])
            close = a is not None and b is not None and 0 <= (b - a).total_seconds() < MERGE_WINDOW_SECONDS
            if prev["speaker"] == m["speaker"] and close:
                prev["original"] = " ".join(x for x in (prev["original"], m["original"]) if x)
                prev["translation"] = " ".join(x for x in (prev["translation"], m["translation"]) if x)
                prev["timestamp"] = m["timestamp"]
                continue
        merged.append(dict(m))
    return merged


def build_meeting_payload(
    item: Dict[str, Any],
    *,
    inline_messages: Optional[List[Dict[str, Any]]] = None,
    now: Optional[datetime] = None,
    model_id: str,
) -> Dict[str, Any]:
    source = inline_messages if inline_messages else (item.get("messages") or [])
    messages = merge_messages(source)
    started = item.get("createdAt") or (messages[0]["timestamp"] if messages else "")
    ended = messages[-1]["timestamp"] if messages else started
    speakers: List[str] = []
    languages: List[str] = []
    for m in messages:
        label = SPEAKER_LABELS.get(m["speaker"], m["speaker"])
        if label not in speakers:
            speakers.append(label)
        if m["detectedLanguage"] and m["detectedLanguage"] not in languages:
            languages.append(m["detectedLanguage"])
    generated = (now or datetime.now(timezone.utc)).isoformat()
    return {
        "meeting": {
            "meetingId": item.get("meetingId", ""),
            "title": item.get("title", ""),
            "startedAt": started,
            "endedAt": ended,
            "speakers": speakers,
            "languages": languages,
            "generatedAt": generated,
            "modelId": model_id,
        },
        "messages": messages,
    }


# ─── DynamoDB helpers ────────────────────────────────────────────────────────

_deserializer = TypeDeserializer()


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def ddb_item_to_dict(item: Dict[str, Any]) -> Dict[str, Any]:
    return {k: _plain(_deserializer.deserialize(v)) for k, v in (item or {}).items()}


def page_key(meeting_id: str, page_id: str) -> str:
    return f"p/{meeting_id}/{page_id}.html"


async def unpublish_page(meeting_id: str, *, s3, ddb, bucket: str, pages_table: str, page_id: str) -> None:
    # S3 삭제가 실패하면 DynamoDB 는 건드리지 않는다 — 링크가 살아 있는데 "해제됨"으로 보이면 안 된다.
    await s3.delete_object(Bucket=bucket, Key=page_key(meeting_id, page_id))
    await ddb.delete_item(TableName=pages_table, Key={"meetingId": {"S": meeting_id}})


async def load_page(ddb, pages_table: str, meeting_id: str) -> Optional[Dict[str, Any]]:
    """The meeting's page record from the pages table, or None."""
    res = await ddb.get_item(TableName=pages_table, Key={"meetingId": {"S": meeting_id}})
    item = ddb_item_to_dict(res.get("Item") or {})
    return item if item.get("pageId") else None


# ─── Request validation ──────────────────────────────────────────────────────

MEETING_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_INLINE_MESSAGES = 2000
MAX_INLINE_CHARS = 400_000


class PageRequestError(Exception):
    """클라이언트 요청이 잘못됐을 때 — 메시지를 그대로 page_stream error 로 보낸다."""


def check_meeting_id(meeting_id: Any) -> None:
    if not isinstance(meeting_id, str) or not MEETING_ID_RE.fullmatch(meeting_id):
        raise PageRequestError("잘못된 미팅 ID입니다")


def check_inline_messages(messages: Any) -> None:
    if messages is None:
        return
    if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
        raise PageRequestError("메시지 형식이 올바르지 않습니다")
    if len(messages) > MAX_INLINE_MESSAGES:
        raise PageRequestError(f"메시지가 너무 많습니다 (최대 {MAX_INLINE_MESSAGES}개)")
    total = sum(len(v) for m in messages for v in m.values() if isinstance(v, str))
    if total > MAX_INLINE_CHARS:
        raise PageRequestError(f"트랜스크립트가 너무 깁니다 (최대 {MAX_INLINE_CHARS:,}자)")


# ─── Dependencies ────────────────────────────────────────────────────────────

@dataclass
class PageAgentDeps:
    agentcore_client: Callable[[], Any]
    s3_client: Callable[[], Any]
    ddb_client: Callable[[], Any]
    runtime_arn: str
    bucket: str
    table: str
    pages_table: str = "transmeet-pages"
    model_id: str = PAGE_AGENT_MODEL_ID
    # 기본 모델의 재시도가 모두 일시 오류로 끝나면 넘어가는 대체 모델. 빈 문자열이면 없음.
    fallback_model_id: str = PAGE_AGENT_FALLBACK_MODEL_ID
    invoke_retries: int = 2
    # 재시도 전 대기(초). 횟수가 남았는데 항목이 모자라면 마지막 값을 반복한다.
    retry_backoff: tuple = (5.0, 15.0)

    @classmethod
    def from_config(cls) -> "PageAgentDeps":
        return cls(
            agentcore_client=aws_clients.agentcore_client,
            s3_client=aws_clients.s3_client,
            ddb_client=aws_clients.dynamodb_client,
            runtime_arn=config.PAGE_AGENT_RUNTIME_ARN,
            bucket=config.PAGES_BUCKET,
            table=config.MEETINGS_TABLE,
            pages_table=config.PAGES_TABLE,
            model_id=config.PAGE_AGENT_MODEL_ID,
            fallback_model_id=config.PAGE_AGENT_FALLBACK_MODEL_ID,
            invoke_retries=config.PAGE_AGENT_INVOKE_RETRIES,
        )


# Bedrock 이 일시적으로 거절했거나 연결이 끊긴 경우. 에이전트의 error 이벤트(transient 플래그)와
# InvokeAgentRuntime 자체의 예외 모두 여기로 분류한다.
_TRANSIENT_ERROR_MARKERS = (
    "serviceunavailableexception",
    "throttlingexception",
    "modelnotreadyexception",
    "internalserverexception",
    "modelstreamerrorexception",
    "bedrock is unable to process",
    "read timed out",
    "readtimeouterror",
    "connection reset",
    "connection aborted",
    "runtimeclienterror",
    "serviceexception",
)


def is_transient_agent_error(obj: Any) -> bool:
    if isinstance(obj, dict):
        if obj.get("transient") is True:
            return True
        text = str(obj.get("error", "")).lower()
    else:
        text = str(obj).lower()
        code = ((getattr(obj, "response", None) or {}).get("Error") or {}).get("Code", "")
        text += " " + str(code).lower()
    return any(m in text for m in _TRANSIENT_ERROR_MARKERS)


# 마지막 종료 이벤트(done/error)를 미팅별로 잠시 기억한다 — 소켓이 바뀐 클라이언트가 page_status 로 되묻는다.
PAGE_RESULT_TTL_S = 30 * 60
_LAST_PAGE_RESULTS: Dict[str, tuple] = {}  # meeting_id -> (monotonic_ts, payload)


def _remember_result(meeting_id: str, payload: Dict[str, Any]) -> None:
    now = time.monotonic()
    for mid, (ts, _) in list(_LAST_PAGE_RESULTS.items()):
        if now - ts > PAGE_RESULT_TTL_S:
            del _LAST_PAGE_RESULTS[mid]
    _LAST_PAGE_RESULTS[meeting_id] = (now, payload)


def page_status_event(meeting_id: str) -> Dict[str, Any]:
    """클라이언트가 '아직 생성 중인가?' 라고 물을 때의 응답.

    돌고 있으면 text 없는 status(클라이언트는 무시), 끝났으면 놓쳤을 done/error 를 그대로 재전송,
    기록이 없으면(프로세스 재시작 등) 오류로 알려 타임아웃을 기다리지 않게 한다.
    """
    running = _ACTIVE_PAGE_TASKS.get(meeting_id)
    if running is not None and not running.done():
        return {"type": "page_stream", "meetingId": meeting_id, "phase": "status"}
    remembered = _LAST_PAGE_RESULTS.get(meeting_id)
    if remembered and time.monotonic() - remembered[0] <= PAGE_RESULT_TTL_S:
        return dict(remembered[1])
    return {"type": "page_stream", "meetingId": meeting_id, "phase": "error",
            "error": "생성이 중단되었습니다. 다시 시도하세요"}


Sender = Callable[[Dict[str, Any]], Awaitable[None]]


def _emitter(send: Sender, meeting_id: str, **tags: Any):
    async def emit(phase: str, **fields: Any) -> None:
        payload = {"type": "page_stream", "meetingId": meeting_id, "phase": phase, **tags, **fields}
        if phase in ("done", "error") and not tags:
            _remember_result(meeting_id, payload)
        try:
            await send(payload)
        except Exception:
            # 소켓이 끊겨도 생성은 끝까지 진행한다 — 결과는 DynamoDB 에 남는다.
            logger.debug("[page] send failed (phase=%s)", phase, exc_info=True)
    return emit


async def _load_meeting(deps: PageAgentDeps, meeting_id: str) -> Dict[str, Any]:
    async with deps.ddb_client() as ddb:
        res = await ddb.get_item(TableName=deps.table, Key={"meetingId": {"S": meeting_id}})
    return ddb_item_to_dict(res.get("Item") or {})


# ─── Runtime stream ──────────────────────────────────────────────────────────

def new_session_id() -> str:
    # Runtime 세션 ID 는 33자 이상이어야 한다
    return uuid4().hex + uuid4().hex[:8]


class AgentError(Exception):
    """The agent reported `phase: error`."""

    def __init__(self, message: str, *, transient: bool):
        super().__init__(message)
        self.message = message
        self.transient = transient


class AgentStreamEnded(Exception):
    """The SSE stream closed without a terminal done/error event."""


async def parse_sse_lines(body: Any) -> AsyncIterator[Dict[str, Any]]:
    """Yield JSON objects from `data: ...` lines; skip blanks, comments and malformed lines."""
    async for raw in body.iter_lines():
        line = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data:
            continue
        try:
            evt = json.loads(data)
        except json.JSONDecodeError:
            logger.warning("[page] malformed SSE line: %.200s", data)
            continue
        if isinstance(evt, dict):
            yield evt


GENERIC_ERROR = "페이지 생성 중 오류가 발생했습니다"


async def _stream_once(ac: Any, deps: PageAgentDeps, payload: Dict[str, Any], emit, meeting_id: str) -> Dict[str, Any]:
    resp = await ac.invoke_agent_runtime(
        agentRuntimeArn=deps.runtime_arn,
        runtimeSessionId=new_session_id(),
        contentType="application/json",
        accept="text/event-stream",
        payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    async for evt in parse_sse_lines(resp["response"]):
        phase = evt.get("phase")
        if phase == "status":
            if evt.get("text"):
                await emit("status", text=evt["text"])
        elif phase == "delta":
            if evt.get("text"):
                await emit("delta", text=evt["text"])
        elif phase == "done":
            page = evt.get("page") or {}
            if evt.get("usage"):
                logger.info("[page] meeting=%s usage=%s", meeting_id, evt["usage"])
            if not page.get("url"):
                raise AgentError(GENERIC_ERROR, transient=False)
            return page
        elif phase == "error":
            raise AgentError(evt.get("error") or GENERIC_ERROR, transient=is_transient_agent_error(evt))
    raise AgentStreamEnded()


async def _published_since(deps: PageAgentDeps, meeting_id: str, since_iso: str) -> Optional[Dict[str, Any]]:
    """The agent may have published before the stream broke — don't generate twice."""
    try:
        async with deps.ddb_client() as ddb:
            page = await load_page(ddb, deps.pages_table, meeting_id)
    except Exception:
        logger.warning("[page] could not re-check page for %s", meeting_id, exc_info=True)
        return None
    if page and page.get("url") and str(page.get("generatedAt", "")) >= since_iso:
        return {k: v for k, v in page.items() if k not in ("meetingId", "updatedAt")}
    return None


async def run_page_generation(
    send: Sender,
    meeting_id: str,
    *,
    inline_messages: Optional[List[Dict[str, Any]]] = None,
    deps: Optional[PageAgentDeps] = None,
) -> Optional[Dict[str, Any]]:
    deps = deps or PageAgentDeps.from_config()
    emit = _emitter(send, meeting_id)
    # 시계 오차 여유를 두고 '이번 생성 이후 발행됐는가' 를 판단한다
    since_iso = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()

    try:
        await emit("status", text="미팅 기록을 불러오는 중")
        item = await _load_meeting(deps, meeting_id)
        if not item:
            await emit("error", error="미팅을 찾을 수 없습니다")
            return None
        payload = build_meeting_payload(item, inline_messages=inline_messages, model_id=deps.model_id)
        if not payload["messages"]:
            await emit("error", error="기록이 없습니다")
            return None

        await emit("status", text="에이전트를 시작합니다")
        failures = 0
        using_fallback = False
        async with deps.agentcore_client() as ac:
            while True:
                try:
                    page = await _stream_once(ac, deps, payload, emit, meeting_id)
                    await emit("done", page=page)
                    return page
                except Exception as e:
                    if isinstance(e, AgentError):
                        transient = e.transient
                    elif isinstance(e, AgentStreamEnded):
                        transient = True
                    else:
                        transient = is_transient_agent_error(e)
                    if not transient:
                        raise
                    # 스트림이 끊기기 전에 에이전트가 이미 발행했을 수 있다
                    already = await _published_since(deps, meeting_id, since_iso)
                    if already:
                        logger.info("[page] stream broke after publish meeting=%s — reporting done", meeting_id)
                        await emit("done", page=already)
                        return already
                    failures += 1
                    if failures > deps.invoke_retries:
                        if using_fallback or not deps.fallback_model_id or deps.fallback_model_id == payload["meeting"]["modelId"]:
                            raise
                        logger.warning("[page] primary model exhausted meeting=%s, switching to %s: %s",
                                       meeting_id, deps.fallback_model_id, e)
                        using_fallback = True
                        failures = 0
                        payload["meeting"]["modelId"] = deps.fallback_model_id
                        await emit("status", text="기본 모델이 응답하지 않아 대체 모델로 생성합니다")
                        continue
                    backoff = deps.retry_backoff
                    delay = backoff[min(failures - 1, len(backoff) - 1)] if backoff else 0
                    logger.warning("[page] transient failure meeting=%s attempt=%d/%d: %s",
                                   meeting_id, failures, deps.invoke_retries, e)
                    await emit("status", text=f"모델이 일시적으로 응답하지 않아 다시 시도합니다 ({failures}/{deps.invoke_retries})")
                    if delay:
                        await asyncio.sleep(delay)
    except AgentError as e:
        logger.warning("[page] agent error meeting=%s: %s", meeting_id, e.message)
        await emit("error", error=e.message)
        return None
    except Exception:
        logger.exception("[page] generation failed meeting=%s", meeting_id)
        await emit("error", error=GENERIC_ERROR)
        return None


# 같은 미팅의 생성은 연결과 무관하게 프로세스 안에서 하나만 돈다.
_ACTIVE_PAGE_TASKS: Dict[str, "asyncio.Task[Optional[Dict[str, Any]]]"] = {}


def start_page_generation(
    send: Sender,
    meeting_id: Any,
    *,
    inline_messages: Any = None,
) -> "asyncio.Task[Optional[Dict[str, Any]]]":
    """요청을 검증하고 생성 태스크를 시작한다. 잘못된 요청·중복 요청은 PageRequestError."""
    check_meeting_id(meeting_id)
    check_inline_messages(inline_messages)
    running = _ACTIVE_PAGE_TASKS.get(meeting_id)
    if running is not None and not running.done():
        raise PageRequestError("이미 생성 중입니다")
    task = asyncio.create_task(run_page_generation(send, meeting_id, inline_messages=inline_messages))
    _ACTIVE_PAGE_TASKS[meeting_id] = task

    def _release(t: "asyncio.Task[Optional[Dict[str, Any]]]") -> None:
        if _ACTIVE_PAGE_TASKS.get(meeting_id) is t:
            del _ACTIVE_PAGE_TASKS[meeting_id]

    task.add_done_callback(_release)
    return task


async def run_unpublish(send: Sender, meeting_id: str, *, deps: Optional[PageAgentDeps] = None) -> None:
    deps = deps or PageAgentDeps.from_config()
    emit = _emitter(send, meeting_id, op="unpublish")
    try:
        async with deps.ddb_client() as ddb:
            page = await load_page(ddb, deps.pages_table, meeting_id)
        if not page:
            await emit("unpublished")
            return
        async with deps.s3_client() as s3, deps.ddb_client() as ddb:
            await unpublish_page(meeting_id, s3=s3, ddb=ddb, bucket=deps.bucket, pages_table=deps.pages_table, page_id=page["pageId"])
        await emit("unpublished")
    except Exception:
        logger.exception("[page] unpublish failed meeting=%s", meeting_id)
        await emit("error", error="공유 해제에 실패했습니다")
