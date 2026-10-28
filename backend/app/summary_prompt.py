"""요약 패널용 프롬프트. REST Lambda도 같은 JSON 정의를 읽는다."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_DEFINITIONS = json.loads(Path(__file__).with_name("summary_styles.json").read_text(encoding="utf-8"))
DEFAULT_SUMMARY_STYLE: str = _DEFINITIONS["defaultStyle"]
SUMMARY_SYSTEM_PROMPT: str = _DEFINITIONS["systemPrompt"]


CUSTOM_SUMMARY_STYLE = "custom"
# 내 양식 본문 길이 상한. src/lib/summaryStyles.ts 의 SUMMARY_TEMPLATE_LIMITS 와 같다.
TEMPLATE_BODY_MAX = {"instruction": 6000, "example": 12000}


def normalize_summary_style(value: Any) -> str:
    """과거 레코드와 알 수 없는 선택값은 기존 상세 요약을 사용한다. 내 양식은 `custom`."""
    if value == CUSTOM_SUMMARY_STYLE:
        return value
    return value if isinstance(value, str) and value in _DEFINITIONS["prompts"] else DEFAULT_SUMMARY_STYLE


def parse_summary_custom(item: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """사본 테이블 항목(DynamoDB 속성 맵). 모양·길이가 틀리면 None — 그때는 detailed 로 만든다."""
    fields = item
    if not isinstance(fields, dict):
        return None
    custom = {k: (fields.get(k) or {}).get("S") for k in ("templateId", "name", "kind", "body", "templateUpdatedAt")}
    kind, body = custom["kind"], custom["body"]
    if kind not in TEMPLATE_BODY_MAX or not isinstance(body, str) or not body.strip() or len(body) > TEMPLATE_BODY_MAX[kind]:
        return None
    if not custom["templateId"] or not custom["name"]:
        return None
    return {k: v or "" for k, v in custom.items()}


def resolve_summary_style(requested: Any, saved: Any, custom: Optional[Dict[str, str]]) -> str:
    """요청 값이 있으면 그것, 없으면 저장값. 내 양식인데 사본이 없으면 detailed."""
    style = normalize_summary_style(requested if requested is not None else saved)
    return DEFAULT_SUMMARY_STYLE if style == CUSTOM_SUMMARY_STYLE and not custom else style


def transcript_lines(messages: Iterable[Dict[str, Any]]) -> List[str]:
    """발언 원문 한 줄씩. 화자도 시각도 넣지 않는다 — 요약 패널은 시각을 보여주지 않고(사용자 결정
    2026-10-09), 입력에 시각이 있으면 모델이 그대로 옮겨 적는다.

    프런트 인라인 메시지(`original`)와 DynamoDB 항목(`originalText`)을 모두 받는다.
    """
    return [t for t in ((m.get("original") or m.get("originalText") or "").strip() for m in messages) if t]


def _custom_body(custom: Dict[str, str]) -> str:
    lines = list(_DEFINITIONS["customWrapper"])
    if custom["kind"] == "example":
        lines += _DEFINITIONS["exampleWrapper"]
    # 긴 견본 뒤에서도 규칙이 약해지지 않게 닫는 태그 바로 뒤에 한 번 더 적는다
    tail = _DEFINITIONS["exampleReminder"] if custom["kind"] == "example" else [""]
    return "\n".join(lines + ["<사용자_양식>", custom["body"], "</사용자_양식>", *tail])


def build_summary_prompt(title: str, transcript: str, style: Any = DEFAULT_SUMMARY_STYLE,
                         custom: Optional[Dict[str, str]] = None) -> str:
    head = f"미팅 제목: {title.strip()}\n\n" if title and title.strip() else ""
    style = resolve_summary_style(style, None, custom)
    body = _custom_body(custom) if style == CUSTOM_SUMMARY_STYLE else "\n".join(_DEFINITIONS["prompts"][style])
    return f"{head}{body}\n---\n{transcript}"
