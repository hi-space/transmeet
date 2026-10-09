"""Deterministic page metadata (header/footer values) and transcript rendering."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Asia/Seoul"


def parse_ts(ts: Any) -> Optional[datetime]:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_local(ts: Any, tz_name: str = DEFAULT_TZ) -> Optional[datetime]:
    dt = parse_ts(ts)
    return dt.astimezone(ZoneInfo(tz_name)) if dt else None


def hhmm(ts: Any, tz_name: str = DEFAULT_TZ) -> str:
    dt = to_local(ts, tz_name)
    return dt.strftime("%H:%M") if dt else ""


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, m = divmod(seconds // 60, 60)
    if h and m:
        return f"{h}시간 {m}분"
    if h:
        return f"{h}시간"
    return f"{m}분"


def derive_meta(meeting: Dict[str, Any], messages: List[Dict[str, Any]], *, tz_name: str = DEFAULT_TZ,
                now: Optional[datetime] = None) -> Dict[str, str]:
    tz = ZoneInfo(tz_name)
    now = now or datetime.now(timezone.utc)
    started = to_local(meeting.get("startedAt"), tz_name) or (to_local(messages[0]["timestamp"], tz_name) if messages else None)
    ended = to_local(meeting.get("endedAt"), tz_name) or (to_local(messages[-1]["timestamp"], tz_name) if messages else None)
    if started and ended and ended < started:
        ended = started
    generated = to_local(meeting.get("generatedAt"), tz_name) or now.astimezone(tz)
    return {
        "TITLE": meeting.get("title") or "미팅록",
        "MEETING_ID": meeting.get("meetingId", ""),
        "MODEL_ID": meeting.get("modelId", ""),
        "DATE": started.strftime("%Y-%m-%d") if started else "",
        "START": started.strftime("%H:%M") if started else "",
        "END": ended.strftime("%H:%M") if ended else "",
        "DURATION": fmt_duration((ended - started).total_seconds()) if started and ended else "미정",
        "GENERATED_AT": generated.isoformat(),
        "GENERATED_AT_LOCAL": generated.strftime("%Y-%m-%d %H:%M") + f" ({tz_name.split('/')[-1]})",
        "RESEARCH_DATE": now.astimezone(tz).strftime("%Y-%m-%d"),
        "TZ": tz_name,
    }


def render_transcript(messages: List[Dict[str, Any]], *, tz_name: str = DEFAULT_TZ) -> str:
    """One line per utterance: `[HH:MM:SS] 원문`.

    화자와 번역은 넣지 않는다 — 페이지는 발언자를 구분하지 않고, 모델은 원문을 직접 읽는다.
    번역을 빼면 모델 입력이 30% 이상 줄어든다. 원문이 비어 있으면 번역으로 대신한다.
    """
    lines = []
    for m in messages:
        dt = to_local(m.get("timestamp"), tz_name)
        t = dt.strftime("%H:%M:%S") if dt else "--:--:--"
        text = (m.get("original") or m.get("translation") or "").replace("\n", " ").strip()
        if text:
            lines.append(f"[{t}] {text}")
    return "\n".join(lines)
