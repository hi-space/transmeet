"""The meeting-page LangGraph.

prepare → outline → [research ∥ diagrams] → insights → assemble → validate → (publish | assemble | fail)

Every model-using node sends the transcript once and runs a short tool loop
(`stage.run_stage`). Deterministic work (header/footer values, timeline SVG, inlining
diagram SVGs, validation, publishing) is done in code, not by the model.
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import operator
import re
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Awaitable, Callable, Dict, List, Literal, Optional, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from . import events
from .meta import derive_meta, render_transcript
from .model import ALLOWED_MODEL_IDS, ainvoke_with_retry, is_transient_error
from .publish import PublishedPage
from .stage import run_stage
from .tools import make_tools
from .validator import validate_html
from .web_search import WebSearchClient, make_web_search_tool
from .workspace import DEFAULT_SKILLS_DIR, DEFAULT_WORK_BASE, Workspace, create_workspace

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
TEMPLATE_REL = "skills/meeting-minutes-page/assets/template.html"
TIMELINE_SCRIPT_REL = Path("meeting-diagrams") / "scripts" / "timeline_svg.py"
STAGE_ITERATIONS = {"research": 20, "insights": 6, "diagrams": 12, "assemble": 25}
MODEL_PLACEHOLDERS = ("TLDR_ITEMS", "DECISIONS", "ACTION_ROWS", "TOPIC_BLOCKS", "OPEN_QUESTIONS", "GLOSSARY_ROWS")
DIAGRAM_MARKER_RE = re.compile(r"\{\{DIAGRAM:([A-Za-z0-9_.-]+)\}\}")
PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_]+\}\}")
HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})")
NO_RESEARCH_TEXT = '<p class="time">리서치 대상이 되는 외부 사실이 없었습니다.</p>'
NO_INSIGHTS_TEXT = '<p class="time">추가 인사이트 없음 — 결정과 액션 아이템이 논의를 충분히 반영합니다.</p>'


# ─── Structured outline ──────────────────────────────────────────────────────

class Topic(BaseModel):
    start: str = Field(description="HH:MM")
    end: str = Field(description="HH:MM")
    title: str = Field(description="12자 이내 명사형 제목")
    summary: str = Field(description="2~3문장 요지")


class ResearchTarget(BaseModel):
    claim: str
    said_at: str = Field(description="HH:MM")
    query: str = Field(description="영어 검색어, 200자 이내")


class DiagramPlan(BaseModel):
    kind: Literal["timeline", "flow", "relations"]
    title: str
    rationale: str


class Outline(BaseModel):
    topics: List[Topic] = Field(default_factory=list)
    research_targets: List[ResearchTarget] = Field(default_factory=list)
    diagram_plan: List[DiagramPlan] = Field(default_factory=list)
    glossary_candidates: List[str] = Field(default_factory=list)


# ─── State ───────────────────────────────────────────────────────────────────

def merge_usage(a: Optional[Dict[str, int]], b: Optional[Dict[str, int]]) -> Dict[str, int]:
    out = dict(a or {})
    for k, v in (b or {}).items():
        out[k] = out.get(k, 0) + int(v)
    return out


class PageState(TypedDict, total=False):
    meeting: Dict[str, Any]
    messages: List[Dict[str, Any]]
    model_id: str
    work_dir: str
    meta: Dict[str, str]
    outline: Dict[str, Any]
    research_html: str
    insights_html: str
    diagram_manifest: List[Dict[str, Any]]
    page_path: str
    validation_errors: List[str]
    assemble_attempts: int
    published: Optional[Dict[str, Any]]
    failed: bool
    usage: Annotated[Dict[str, int], merge_usage]
    stage_errors: Annotated[List[str], operator.add]


Publisher = Callable[[str, str, str, str], Awaitable[PublishedPage]]
LlmFactory = Callable[[str, str], Any]  # (model_id, stage) -> chat model


@dataclass
class GraphDeps:
    llm_factory: LlmFactory
    publisher: Publisher
    web_search: Optional[WebSearchClient] = None
    skills_dir: Path = DEFAULT_SKILLS_DIR
    work_base: Path = DEFAULT_WORK_BASE
    max_iterations: Dict[str, int] = field(default_factory=lambda: dict(STAGE_ITERATIONS))
    max_assemble_attempts: int = 3
    max_bytes: int = 1_500_000
    tz_name: str = "Asia/Seoul"


class PayloadError(ValueError):
    """The invocation payload is unusable (not retryable)."""


def initial_state(payload: Dict[str, Any], session_id: str, deps: GraphDeps) -> PageState:
    meeting = payload.get("meeting") if isinstance(payload, dict) else None
    messages = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(meeting, dict) or not str(meeting.get("meetingId", "")).strip():
        raise PayloadError("meeting.meetingId is required")
    if not isinstance(messages, list) or not messages:
        raise PayloadError("기록이 없습니다")
    model_id = meeting.get("modelId") or ALLOWED_MODEL_IDS[0]
    if model_id not in ALLOWED_MODEL_IDS:
        raise PayloadError(f"model not allowed: {model_id}")
    ws = create_workspace(session_id, base=deps.work_base, skills_dir=deps.skills_dir)
    ws.write_json("meeting.json", meeting)
    ws.write_json("transcript.json", messages)
    ws.write_text("transcript.txt", render_transcript(messages, tz_name=deps.tz_name))
    return PageState(
        meeting=meeting, messages=messages, model_id=model_id, work_dir=str(ws.root),
        assemble_attempts=0, validation_errors=[], published=None, failed=False, usage={}, stage_errors=[],
    )


# ─── Helpers ─────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def system_prompt(stage: str) -> str:
    return _prompt("common") + "\n\n" + _prompt(stage)


def _ws(state: PageState, deps: GraphDeps) -> Workspace:
    return Workspace(root=Path(state["work_dir"]), skills_dir=deps.skills_dir)


def _transcript_block(ws: Workspace, state: PageState) -> str:
    meta = state.get("meta") or {}
    head = (f"미팅: {meta.get('TITLE', '')} · {meta.get('DATE', '')} {meta.get('START', '')}–{meta.get('END', '')} "
            f"({meta.get('DURATION', '')}) · 참석 {meta.get('SPEAKERS', '')} · 언어 {meta.get('LANGS', '')}")
    return f"{head}\n\n### 트랜스크립트 (transcript.txt)\n```\n{ws.read_text('transcript.txt')}\n```"


def _topics_block(outline: Dict[str, Any]) -> str:
    lines = [f"{i}. [{t.get('start', '')}–{t.get('end', '')}] {t.get('title', '')} — {t.get('summary', '')}"
             for i, t in enumerate(outline.get("topics") or [], start=1)]
    return "### 주제 분할 (outline.json → topic-N 번호)\n" + ("\n".join(lines) or "(없음)")


def _hhmm(value: str, fallback: str) -> str:
    m = HHMM_RE.match((value or "").strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else fallback


def _figure(svg: str, caption: str) -> str:
    cap = f"<figcaption>{html_lib.escape(caption)}</figcaption>" if caption else ""
    return f"<figure>{svg.strip()}{cap}</figure>"


def _escape(value: str) -> str:
    return html_lib.escape(value or "", quote=False)


# ─── Graph ───────────────────────────────────────────────────────────────────

def build_graph(deps: GraphDeps):
    def llm(state: PageState, stage: str):
        return deps.llm_factory(state["model_id"], stage)

    async def prepare(state: PageState) -> PageState:
        events.emit(events.status("미팅 기록을 준비합니다"))
        meta = derive_meta(state["meeting"], state["messages"], tz_name=deps.tz_name)
        return {"meta": meta}

    async def outline(state: PageState) -> PageState:
        ws = _ws(state, deps)
        events.emit(events.status("논의 구조를 파악합니다"))
        structured = llm(state, "outline").with_structured_output(Outline)
        msgs = [SystemMessage(content=system_prompt("outline")), HumanMessage(content=_transcript_block(ws, state))]
        result: Any = None
        for attempt in range(2):
            try:
                result = await ainvoke_with_retry(structured, msgs)
                break
            except Exception as e:
                if is_transient_error(e) or attempt == 1:
                    raise
                logger.warning("[outline] structured output failed once, retrying: %s", e)
        data = result.model_dump() if hasattr(result, "model_dump") else dict(result or {})
        meta = state["meta"]
        if not data.get("topics"):
            data["topics"] = [{"start": meta["START"], "end": meta["END"], "title": meta["TITLE"][:12], "summary": "미팅 전체"}]
        for t in data["topics"]:
            t["start"], t["end"] = _hhmm(t.get("start", ""), meta["START"]), _hhmm(t.get("end", ""), meta["END"])
        if not any(d.get("kind") == "timeline" for d in data.get("diagram_plan") or []):
            data.setdefault("diagram_plan", []).insert(0, {"kind": "timeline", "title": "주제 타임라인", "rationale": "기본"})
        ws.write_json("outline.json", data)
        events.emit(events.status(f"논의 흐름을 {len(data['topics'])}개 주제로 나눴습니다"))
        usage = getattr(result, "usage_metadata", None) or {}
        return {"outline": data, "usage": {k: v for k, v in usage.items() if isinstance(v, int)}}

    async def research(state: PageState) -> PageState:
        ws = _ws(state, deps)
        targets = state["outline"].get("research_targets") or []
        meta = state["meta"]
        if not targets:
            ws.write_text("research.html", NO_RESEARCH_TEXT)
            events.emit(events.status("리서치 대상이 되는 외부 사실이 없습니다"))
            return {"research_html": NO_RESEARCH_TEXT}
        events.emit(events.status(f"리서치 대상 {len(targets)}개를 검색합니다"))
        tools = make_tools(ws, ["load_skill", "read_file", "write_file"])
        if deps.web_search is not None:
            tools.append(make_web_search_tool(deps.web_search))
        user = (f"{_transcript_block(ws, state)}\n\n### 리서치 대상 (outline.json → research_targets)\n"
                f"```json\n{json.dumps(targets, ensure_ascii=False, indent=1)}\n```\n\n"
                f"RESEARCH_DATE = {meta['RESEARCH_DATE']}. 산출물: `research.html`.")
        res = await run_stage(name="research", llm=llm(state, "research"), system_prompt=system_prompt("research"),
                              user_prompt=user, tools=tools, max_iterations=deps.max_iterations["research"])
        if ws.exists("research.html") and ws.read_text("research.html").strip():
            html = ws.read_text("research.html")
        else:
            logger.warning("[research] no research.html produced; writing 확인 불가 fallback")
            items = "".join(
                f'<div class="ref card"><div>{_escape(t.get("claim", ""))} <span class="badge na">확인 불가</span></div>'
                f'<div class="why">미팅 [{_escape(t.get("said_at", ""))}] — 공식 출처를 찾지 못했습니다.</div></div>'
                for t in targets)
            html = items + f'<p class="time">조회일 {meta["RESEARCH_DATE"]} · Web Search {deps.web_search.calls if deps.web_search else 0}회</p>'
            ws.write_text("research.html", html)
        return {"research_html": html, "usage": res.usage}

    def _render_timeline(ws: Workspace, state: PageState) -> bool:
        meta, topics = state["meta"], state["outline"].get("topics") or []
        data = {"start": meta["START"], "end": meta["END"],
                "topics": [{"label": (t.get("title") or "")[:12], "start": t["start"], "end": t["end"]} for t in topics]}
        if not data["start"] or not data["end"] or not data["topics"]:
            return False
        ws.write_json("diagrams/timeline.json", data)
        script = ws.skills_dir / TIMELINE_SCRIPT_REL
        try:
            proc = subprocess.run([sys.executable, str(script), "diagrams/timeline.json"], cwd=ws.root,
                                  capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("[diagrams] timeline script failed: %s", e)
            return False
        if proc.returncode != 0 or "<svg" not in proc.stdout:
            logger.warning("[diagrams] timeline script error: %s", proc.stderr[:500])
            return False
        ws.write_text("diagrams/timeline.svg", proc.stdout)
        return True

    async def diagrams(state: PageState) -> PageState:
        ws = _ws(state, deps)
        plan = [d for d in (state["outline"].get("diagram_plan") or []) if d.get("kind") in ("flow", "relations")]
        has_timeline = _render_timeline(ws, state)
        usage: Dict[str, int] = {}
        if plan:
            events.emit(events.status(f"다이어그램 {len(plan)}개를 그립니다"))
            tools = make_tools(ws, ["load_skill", "bash", "read_file", "write_file"])
            user = (f"{_transcript_block(ws, state)}\n\n{_topics_block(state['outline'])}\n\n### 다이어그램 계획\n"
                    f"```json\n{json.dumps(plan, ensure_ascii=False, indent=1)}\n```")
            res = await run_stage(name="diagrams", llm=llm(state, "diagrams"), system_prompt=system_prompt("diagrams"),
                                  user_prompt=user, tools=tools, max_iterations=deps.max_iterations["diagrams"])
            usage = res.usage
        manifest: List[Dict[str, Any]] = []
        if ws.exists("diagrams/manifest.json"):
            try:
                raw = ws.read_json("diagrams/manifest.json")
                manifest = [m for m in raw if isinstance(m, dict) and m.get("file")] if isinstance(raw, list) else []
            except (ValueError, OSError):
                logger.warning("[diagrams] manifest.json unreadable")
        manifest = [m for m in manifest if m.get("kind") != "timeline" and ws.exists(f"diagrams/{Path(str(m['file'])).name}")]
        if has_timeline:
            manifest.insert(0, {"file": "timeline.svg", "kind": "timeline", "caption": "주제 타임라인"})
        events.emit(events.status(f"다이어그램 {len(manifest)}개를 만들었습니다"))
        return {"diagram_manifest": manifest, "usage": usage}

    async def insights(state: PageState) -> PageState:
        ws = _ws(state, deps)
        events.emit(events.status("인사이트를 정리합니다"))
        tools = make_tools(ws, ["load_skill", "read_file", "write_file"])
        user = (f"{_transcript_block(ws, state)}\n\n{_topics_block(state['outline'])}\n\n"
                f"### 리서치 결과 (research.html)\n```html\n{state.get('research_html', '')}\n```\n\n산출물: `insights.html`.")
        res = await run_stage(name="insights", llm=llm(state, "insights"), system_prompt=system_prompt("insights"),
                              user_prompt=user, tools=tools, max_iterations=deps.max_iterations["insights"])
        if ws.exists("insights.html") and ws.read_text("insights.html").strip():
            html = ws.read_text("insights.html")
        else:
            logger.warning("[insights] no insights.html produced; using empty text")
            html = NO_INSIGHTS_TEXT
            ws.write_text("insights.html", html)
        return {"insights_html": html, "usage": res.usage}

    def _seed_page(ws: Workspace, state: PageState) -> None:
        meta = state["meta"]
        html = ws.read_text(TEMPLATE_REL)
        for key, value in meta.items():
            html = html.replace(f"{{{{{key}}}}}", _escape(value))
        html = html.replace("{{REFERENCES}}", state.get("research_html") or NO_RESEARCH_TEXT)
        html = html.replace("{{INSIGHTS}}", state.get("insights_html") or NO_INSIGHTS_TEXT)
        timeline = next((m for m in state.get("diagram_manifest") or [] if m.get("kind") == "timeline"), None)
        figure = _figure(ws.read_text("diagrams/timeline.svg"), timeline.get("caption", "")) if timeline else ""
        html = html.replace("{{TIMELINE_FIGURE}}", figure)
        ws.write_text("page.html", html)

    def _inline_diagrams(ws: Workspace, state: PageState) -> None:
        html = ws.read_text("page.html")
        by_file = {m["file"]: m for m in state.get("diagram_manifest") or []}

        def repl(m: re.Match) -> str:
            name = Path(m.group(1)).name
            if not ws.exists(f"diagrams/{name}"):
                return ""
            return _figure(ws.read_text(f"diagrams/{name}"), (by_file.get(name) or {}).get("caption", ""))

        ws.write_text("page.html", DIAGRAM_MARKER_RE.sub(repl, html))

    async def assemble(state: PageState) -> PageState:
        ws = _ws(state, deps)
        attempts = state.get("assemble_attempts", 0)
        errors = state.get("validation_errors") or []
        if attempts == 0 or not ws.exists("page.html"):
            _seed_page(ws, state)
        events.emit(events.status("페이지를 조립합니다" if attempts == 0 else f"검증 오류를 고칩니다 ({attempts}/{deps.max_assemble_attempts - 1})"))
        tools = make_tools(ws, ["load_skill", "read_file", "write_file", "edit_file", "bash"])
        extra = [m for m in state.get("diagram_manifest") or [] if m.get("kind") != "timeline"]
        user = (f"{_transcript_block(ws, state)}\n\n{_topics_block(state['outline'])}\n\n"
                f"### 채울 자리표시자\n{', '.join('{{' + p + '}}' for p in MODEL_PLACEHOLDERS)}\n\n"
                f"### 다이어그램 manifest (flow/relations)\n```json\n{json.dumps(extra, ensure_ascii=False)}\n```\n\n"
                f"### 용어 후보\n{', '.join(state['outline'].get('glossary_candidates') or []) or '(없음)'}\n\n"
                f"페이지 제목: {state['meta']['TITLE']}")
        if errors:
            user += ("\n\n### 이전 시도의 검증 오류 — `page.html` 은 그대로 있으니 `edit_file` 로 고치세요\n"
                     + "\n".join(f"- {e}" for e in errors[:12]))
        res = await run_stage(name="assemble", llm=llm(state, "assemble"), system_prompt=system_prompt("assemble"),
                              user_prompt=user, tools=tools, max_iterations=deps.max_iterations["assemble"])
        if ws.exists("page.html"):
            _inline_diagrams(ws, state)
        if not ws.exists("page.meta.json"):
            ws.write_json("page.meta.json", {"title": state["meta"]["TITLE"], "share_summary": ""})
        return {"page_path": "page.html", "assemble_attempts": attempts + 1, "usage": res.usage}

    async def validate(state: PageState) -> PageState:
        ws = _ws(state, deps)
        if not ws.exists("page.html"):
            errors = ["page.html was not written"]
        else:
            html = ws.read_text("page.html")
            errors = validate_html(html, deps.max_bytes)
            leftover = sorted(set(PLACEHOLDER_RE.findall(html)))
            if leftover:
                errors.append("unfilled placeholders: " + ", ".join(leftover))
        if errors:
            events.emit(events.status("검증 실패, 에이전트가 수정합니다"))
        else:
            events.emit(events.status("검증을 통과했습니다"))
        return {"validation_errors": errors}

    def after_validate(state: PageState) -> str:
        if not state.get("validation_errors"):
            return "publish"
        if state.get("assemble_attempts", 0) >= deps.max_assemble_attempts:
            return "fail"
        return "assemble"

    async def publish(state: PageState) -> PageState:
        ws = _ws(state, deps)
        html = ws.read_text("page.html")
        meta_json = ws.read_json("page.meta.json") if ws.exists("page.meta.json") else {}
        title = (meta_json.get("title") or state["meta"]["TITLE"]).strip() if isinstance(meta_json, dict) else state["meta"]["TITLE"]
        summary = (meta_json.get("share_summary") or "") if isinstance(meta_json, dict) else ""
        events.emit(events.status("페이지를 발행합니다"))
        page = await deps.publisher(state["meeting"]["meetingId"], html, title, summary)
        events.emit(events.status("페이지가 발행되었습니다"))
        return {"published": page.to_item()}

    async def fail(state: PageState) -> PageState:
        errors = state.get("validation_errors") or ["unknown"]
        msg = "페이지 발행에 실패했습니다: " + "; ".join(errors[:3])
        events.emit(events.error(msg, stage="assemble"))
        return {"failed": True, "stage_errors": [msg]}

    g = StateGraph(PageState)
    for name, fn in [("prepare", prepare), ("outline", outline), ("research", research), ("diagrams", diagrams),
                     ("insights", insights), ("assemble", assemble), ("validate", validate), ("publish", publish), ("fail", fail)]:
        g.add_node(name, fn)
    g.add_edge(START, "prepare")
    g.add_edge("prepare", "outline")
    g.add_edge("outline", "research")
    g.add_edge("outline", "diagrams")
    g.add_edge(["research", "diagrams"], "insights")
    g.add_edge("insights", "assemble")
    g.add_edge("assemble", "validate")
    g.add_conditional_edges("validate", after_validate, {"publish": "publish", "assemble": "assemble", "fail": "fail"})
    g.add_edge("publish", END)
    g.add_edge("fail", END)
    return g.compile()
