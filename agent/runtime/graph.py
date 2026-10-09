"""The meeting-page LangGraph.

prepare → analyze → [research ∥ diagrams] → insights → assemble → validate → (publish | assemble | fail)

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
from .model import ALLOWED_MODEL_IDS, astream_with_retry, is_transient_error
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
MODEL_PLACEHOLDERS = ("TLDR_ITEMS", "DECISIONS", "ACTION_ROWS", "TOPIC_BLOCKS", "OPEN_QUESTIONS")
# 코드가 채우는 자리표시자 중 모델이 조립을 마친 뒤에야 알 수 있는 것(통계)
CODE_FINAL_PLACEHOLDERS = ("STATS",)
TOC_SECTIONS = [("sec-tldr", "한눈에 보기"), ("sec-summary", "회의 요약"), ("sec-decisions", "결정 사항"), ("sec-actions", "액션 아이템"),
                ("sec-discussion", "논의 흐름"), ("sec-open", "미결 사항"), ("sec-references", "관련 공식 자료"),
                ("sec-insights", "인사이트 보고서"), ("sec-glossary", "용어 사전")]
NO_TERMS_ROW = '<tr><td colspan="3" class="time">해당 없음</td></tr>'
NO_SUMMARY_TEXT = '<p class="time">요약 없음</p>'
EMPTY_ITEM_RE = re.compile(r"결정된 사항 없음|해당 없음|없습니다|없음")
DIAGRAM_MARKER_RE = re.compile(r"\{\{DIAGRAM:([A-Za-z0-9_.-]+)\}\}")
PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_]+\}\}")
HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})")
NO_RESEARCH_TEXT = '<p class="time">리서치 대상이 되는 외부 사실이 없었습니다.</p>'
NO_INSIGHTS_TEXT = '<p class="time">추가 인사이트 없음 — 결정과 액션 아이템이 논의를 충분히 반영합니다.</p>'


# ─── Structured analysis (analyze 단계의 도구 스키마) ──────────────────────────

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


class Term(BaseModel):
    term: str = Field(description="정식 표기 (예: MACC, Databricks, EDP)")
    variants: List[str] = Field(default_factory=list, description="녹취에 나온 다른/잘못된 표기 (예: MSCC, 데이터베이스)")
    meaning: str = Field(description="이 미팅 맥락에서의 뜻, 한 줄 한국어")
    count: int = Field(default=1, description="언급 횟수(변형 표기 포함, 대략)")


class Analysis(BaseModel):
    summary: List[str] = Field(default_factory=list, description="회의 전체 서술 요약, 문단 3~6개")
    terms: List[Term] = Field(default_factory=list)
    topics: List[Topic] = Field(default_factory=list)
    research_targets: List[ResearchTarget] = Field(default_factory=list)
    diagram_plan: List[DiagramPlan] = Field(default_factory=list)


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
    analysis: Dict[str, Any]
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
    head = f"미팅: {meta.get('TITLE', '')} · {meta.get('DATE', '')} {meta.get('START', '')}–{meta.get('END', '')} ({meta.get('DURATION', '')})"
    return f"{head}\n\n### 트랜스크립트 (transcript.txt)\n```\n{ws.read_text('transcript.txt')}\n```"


def _topics_block(analysis: Dict[str, Any]) -> str:
    lines = [f"{i}. [{t.get('start', '')}–{t.get('end', '')}] {t.get('title', '')} — {t.get('summary', '')}"
             for i, t in enumerate(analysis.get("topics") or [], start=1)]
    return "### 주제 분할 (analysis.json → topic-N 번호)\n" + ("\n".join(lines) or "(없음)")


def _terms_block(analysis: Dict[str, Any]) -> str:
    """analyze 단계가 찾은 용어의 정식 표기. 모든 후속 단계가 본문·인용에 이 표기만 쓴다."""
    terms = analysis.get("terms") or []
    if not terms:
        return "### 용어 표기 (analysis.json → terms)\n(없음)"
    lines = []
    for t in terms:
        variants = ", ".join(v for v in (t.get("variants") or []) if v and v != t.get("term"))
        lines.append(f"- **{t.get('term', '')}**" + (f" (녹취 표기: {variants})" if variants else "") + f" — {t.get('meaning', '')}")
    return ("### 용어 표기 (analysis.json → terms)\n본문·인용·캡션에서는 아래 **정식 표기만** 씁니다. 녹취 표기(음성 인식 오류)가 "
            "인용 원문에 있으면 정식 표기로 바로잡습니다.\n" + "\n".join(lines))


def _summary_html(analysis: Dict[str, Any]) -> str:
    paras = [p.strip() for p in (analysis.get("summary") or []) if isinstance(p, str) and p.strip()]
    return "".join(f"<p>{_escape(p)}</p>" for p in paras) or NO_SUMMARY_TEXT


def _glossary_rows(analysis: Dict[str, Any]) -> str:
    rows = []
    for t in sorted(analysis.get("terms") or [], key=lambda x: -int(x.get("count") or 0)):
        term = (t.get("term") or "").strip()
        if not term:
            continue
        variants = ", ".join(_escape(v) for v in (t.get("variants") or []) if v and v != term)
        rows.append(f'<tr><td><span class="term">{_escape(term)}</span></td><td>{_escape(t.get("meaning") or "")}</td>'
                    f'<td class="time">{variants or "—"}</td></tr>')
    return "".join(rows) or NO_TERMS_ROW


def _toc_html(analysis: Dict[str, Any]) -> str:
    items = []
    for n, (sec_id, label) in enumerate(TOC_SECTIONS, start=1):
        sub = ""
        if sec_id == "sec-discussion":
            topics = analysis.get("topics") or []
            sub = "<ol>" + "".join(
                f'<li><a href="#topic-{i}">{_escape(t.get("title") or "")}<span class="time">{_escape(t.get("start") or "")}</span></a></li>'
                for i, t in enumerate(topics, start=1)) + "</ol>" if topics else ""
        items.append(f'<li><a href="#{sec_id}"><span class="n">{n}</span>{label}</a>{sub}</li>')
    return "<ol>" + "".join(items) + "</ol>"


def _section_body(html: str, section: str) -> str:
    m = re.search(rf'<section[^>]*data-section="{section}"[^>]*>(.*?)</section>', html, re.S)
    return m.group(1) if m else ""


def _count_items(body: str, tag: str) -> int:
    """`tag` 요소 수 — 비어 있음을 뜻하는 자리표시 항목(해당 없음 등)은 세지 않는다."""
    n = 0
    for m in re.finditer(rf"<{tag}\b[^>]*>(.*?)</{tag}>", body, re.S):
        text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if text and not (len(text) <= 24 and EMPTY_ITEM_RE.search(text)):
            n += 1
    return n


def _stats_html(html: str) -> str:
    topics = len(re.findall(r'class="topic"', html))
    decisions = _count_items(_section_body(html, "decisions"), "li")
    actions = _count_items(_section_body(html, "action-items"), "tr") - (1 if "<thead" in _section_body(html, "action-items") else 0)
    open_q = _count_items(_section_body(html, "open-questions"), "li")
    refs = len(re.findall(r'class="ref', _section_body(html, "references")))
    cells = [(topics, "주제"), (decisions, "결정"), (max(actions, 0), "액션"), (open_q, "미결"), (refs, "참고 자료")]
    return "".join(f"<li><b>{n}</b><span>{label}</span></li>" for n, label in cells)


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

    async def analyze(state: PageState) -> PageState:
        ws = _ws(state, deps)
        events.emit(events.status("녹취록을 분석합니다 — 용어·주제·전체 요약"))
        # 구조화 출력은 Analysis 도구 호출로 받는다(Opus 5.5 는 Bedrock 에서 tool_choice 강제를 지원하지 않아 auto + 프롬프트 지시).
        # 스트리밍이라 긴 첫 토큰 대기에도 끊기지 않는다.
        bound = llm(state, "analyze").bind_tools([Analysis])
        msgs = [SystemMessage(content=system_prompt("analyze")), HumanMessage(content=_transcript_block(ws, state))]
        result: Any = None
        for attempt in range(2):
            ai = await astream_with_retry(bound, msgs)
            calls = [c for c in (ai.tool_calls or []) if c.get("name") == "Analysis"] or list(ai.tool_calls or [])
            try:
                if not calls:
                    raise ValueError("model returned no Analysis tool call")
                result = Analysis.model_validate(calls[0].get("args") or {})
                break
            except Exception as e:
                if attempt == 1:
                    raise
                logger.warning("[analyze] unusable structured output, retrying once: %s", e)
                msgs = msgs + [ai, HumanMessage(content=f"출력이 스키마에 맞지 않았습니다 ({e}). Analysis 도구를 스키마대로 다시 호출하세요.")]
        data = result.model_dump() if hasattr(result, "model_dump") else dict(result or {})
        meta = state["meta"]
        if not data.get("topics"):
            data["topics"] = [{"start": meta["START"], "end": meta["END"], "title": meta["TITLE"][:12], "summary": "미팅 전체"}]
        for t in data["topics"]:
            t["start"], t["end"] = _hhmm(t.get("start", ""), meta["START"]), _hhmm(t.get("end", ""), meta["END"])
        if not any(d.get("kind") == "timeline" for d in data.get("diagram_plan") or []):
            data.setdefault("diagram_plan", []).insert(0, {"kind": "timeline", "title": "주제 타임라인", "rationale": "기본"})
        ws.write_json("analysis.json", data)
        events.emit(events.status(f"용어 {len(data.get('terms') or [])}개를 정리하고 논의 흐름을 {len(data['topics'])}개 주제로 나눴습니다"))
        usage = getattr(ai, "usage_metadata", None) or {}
        return {"analysis": data, "usage": {k: v for k, v in usage.items() if isinstance(v, int)}}

    async def research(state: PageState) -> PageState:
        ws = _ws(state, deps)
        targets = state["analysis"].get("research_targets") or []
        meta = state["meta"]
        if not targets:
            ws.write_text("research.html", NO_RESEARCH_TEXT)
            events.emit(events.status("리서치 대상이 되는 외부 사실이 없습니다"))
            return {"research_html": NO_RESEARCH_TEXT}
        events.emit(events.status(f"리서치 대상 {len(targets)}개를 검색합니다"))
        tools = make_tools(ws, ["load_skill", "read_file", "write_file"])
        if deps.web_search is not None:
            tools.append(make_web_search_tool(deps.web_search))
        user = (f"{_transcript_block(ws, state)}\n\n{_terms_block(state['analysis'])}\n\n"
                f"### 리서치 대상 (analysis.json → research_targets)\n"
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
        meta, topics = state["meta"], state["analysis"].get("topics") or []
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
        plan = [d for d in (state["analysis"].get("diagram_plan") or []) if d.get("kind") in ("flow", "relations")]
        has_timeline = _render_timeline(ws, state)
        usage: Dict[str, int] = {}
        if plan:
            events.emit(events.status(f"다이어그램 {len(plan)}개를 그립니다"))
            tools = make_tools(ws, ["load_skill", "bash", "read_file", "write_file"])
            user = (f"{_transcript_block(ws, state)}\n\n{_terms_block(state['analysis'])}\n\n{_topics_block(state['analysis'])}\n\n### 다이어그램 계획\n"
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
        user = (f"{_transcript_block(ws, state)}\n\n{_terms_block(state['analysis'])}\n\n{_topics_block(state['analysis'])}\n\n"
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
        analysis = state.get("analysis") or {}
        html = html.replace("{{REFERENCES}}", state.get("research_html") or NO_RESEARCH_TEXT)
        html = html.replace("{{INSIGHTS}}", state.get("insights_html") or NO_INSIGHTS_TEXT)
        html = html.replace("{{SUMMARY}}", _summary_html(analysis))
        html = html.replace("{{GLOSSARY_ROWS}}", _glossary_rows(analysis))
        html = html.replace("{{TOC}}", _toc_html(analysis))
        timeline = next((m for m in state.get("diagram_manifest") or [] if m.get("kind") == "timeline"), None)
        figure = _figure(ws.read_text("diagrams/timeline.svg"), timeline.get("caption", "")) if timeline else ""
        html = html.replace("{{TIMELINE_FIGURE}}", figure)
        ws.write_text("page.html", html)

    def _finalize_page(ws: Workspace, state: PageState) -> None:
        """모델 조립 뒤 코드 마무리: 다이어그램 마커를 <figure> 로 바꾸고 헤더 통계를 채운다."""
        html = ws.read_text("page.html")
        by_file = {m["file"]: m for m in state.get("diagram_manifest") or []}

        def repl(m: re.Match) -> str:
            name = Path(m.group(1)).name
            if not ws.exists(f"diagrams/{name}"):
                return ""
            return _figure(ws.read_text(f"diagrams/{name}"), (by_file.get(name) or {}).get("caption", ""))

        html = DIAGRAM_MARKER_RE.sub(repl, html)
        html = html.replace("{{STATS}}", _stats_html(html))
        ws.write_text("page.html", html)

    async def assemble(state: PageState) -> PageState:
        ws = _ws(state, deps)
        attempts = state.get("assemble_attempts", 0)
        errors = state.get("validation_errors") or []
        if attempts == 0 or not ws.exists("page.html"):
            _seed_page(ws, state)
        events.emit(events.status("페이지를 조립합니다" if attempts == 0 else f"검증 오류를 고칩니다 ({attempts}/{deps.max_assemble_attempts - 1})"))
        tools = make_tools(ws, ["load_skill", "read_file", "write_file", "edit_file", "bash"])
        extra = [m for m in state.get("diagram_manifest") or [] if m.get("kind") != "timeline"]
        user = (f"{_transcript_block(ws, state)}\n\n{_topics_block(state['analysis'])}\n\n"
                f"{_terms_block(state['analysis'])}\n\n"
                f"### 채울 자리표시자\n{', '.join('{{' + p + '}}' for p in MODEL_PLACEHOLDERS)}\n"
                f"(`{{{{STATS}}}}` 와 `{{{{DIAGRAM:...}}}}` 는 코드가 채우니 건드리지 않습니다)\n\n"
                f"### 다이어그램 manifest (flow/relations)\n```json\n{json.dumps(extra, ensure_ascii=False)}\n```\n\n"
                f"페이지 제목: {state['meta']['TITLE']}")
        if errors:
            user += ("\n\n### 이전 시도의 검증 오류 — `page.html` 은 그대로 있으니 `edit_file` 로 고치세요\n"
                     + "\n".join(f"- {e}" for e in errors[:12]))
        res = await run_stage(name="assemble", llm=llm(state, "assemble"), system_prompt=system_prompt("assemble"),
                              user_prompt=user, tools=tools, max_iterations=deps.max_iterations["assemble"])
        if ws.exists("page.html"):
            _finalize_page(ws, state)
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
    for name, fn in [("prepare", prepare), ("analyze", analyze), ("research", research), ("diagrams", diagrams),
                     ("insights", insights), ("assemble", assemble), ("validate", validate), ("publish", publish), ("fail", fail)]:
        g.add_node(name, fn)
    g.add_edge(START, "prepare")
    g.add_edge("prepare", "analyze")
    g.add_edge("analyze", "research")
    g.add_edge("analyze", "diagrams")
    g.add_edge(["research", "diagrams"], "insights")
    g.add_edge("insights", "assemble")
    g.add_edge("assemble", "validate")
    g.add_conditional_edges("validate", after_validate, {"publish": "publish", "assemble": "assemble", "fail": "fail"})
    g.add_edge("publish", END)
    g.add_edge("fail", END)
    return g.compile()
