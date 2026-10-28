"""The `visual` page style: built in stages instead of one long model turn.

plan → (write ∥ draw×N) → review → render

- plan   : picks the page skeleton and collects every concrete fact per topic (JSON).
- write  : writes all page text from the plan (JSON). Tone rules live only here and in review.
- draw   : one SVG per call, all in parallel (the overview gets a wide and a tall version).
- review : checks the text against the transcript and returns path → replacement fixes.
- render : code fills a fixed layout (`assets/visual.css`) — section order and names live here.

A single call that designs, writes and draws the whole page ran out of output tokens and squeezed
the text to ~11% of the page; splitting lets the writer spend its whole output on text.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from . import diagram_design, events
from .model import astream_with_retry
from .stage import message_text
from .validator import validate_html
from .workspace import DEFAULT_SKILLS_DIR

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"
MAX_PARALLEL_DRAWS = 10

# 코드가 먼저 찾아 리뷰어에게 넘기는 의심 구간(실제 위반인지는 리뷰어가 판단).
TONE_PATTERNS = [
    (re.compile(r"(한다|했다|됐다|된다|있다|없다|이다|였다)[.)\s]*$|(한다|했다|됐다|이다)\. "), "해라체 끝맺음"),
    (re.compile(r"[가-힣](함|음|임)[.)]?\s*$"), "공문서체 끝맺음"),
    (re.compile(r"에 대한|를 통해|을 통해|에 있어서|의 경우|하는 것이 필요|되어지|라는 점에서|할 수 있을 것"), "번역체"),
    (re.compile(r"질문이 나왔|의견이 있었|논의 끝에|평가가 나왔|이야기가 나왔|설명됐|제시됐|정리됐"), "회의 경과 서술"),
    (re.compile(r"포커스|얼라인|싱크하"), "녹취 말투"),
    (re.compile(r"\[\d{1,2}:\d{2}"), "발언 시각"),
]
# 분석 메모의 `Jetson Xavier 는` 같은 띄어쓰기를 `Jetson Xavier는` 으로 붙인다(영문·숫자·닫는 괄호 뒤 조사).
PARTICLE_GAP_RE = re.compile(r"(?<=[A-Za-z0-9)])\s+(은|는|이|가|을|를|의|에|에서|에게|로|으로|와|과|도|만|까지|부터|보다|처럼)(?=[\s,.)!?]|$)")
_HIGHLIGHT_TAG_RE = re.compile(r"&lt;(/?)(b|mark)&gt;")
_MARK_TONE_RE = re.compile(r'&lt;mark class="(ok|risk)"&gt;')
VERDICT = {"ok": ("ok", "일치"), "diff": ("diff", "다름"), "na": ("na", "확인 불가")}
DRAW_LABEL = {"overview-wide": '개요 그림 넓은 판(class="ov-wide")', "overview-tall": '개요 그림 세로 판(class="ov-tall")',
              "topic": "주제 그림"}
SVG_SKELETON = ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                '<meta name="transmeet:meeting-id" content="x"><meta name="transmeet:generated-at" content="x"><meta name="transmeet:model" content="x">'
                '<meta name="transmeet:style" content="visual"><title>x</title></head><body><header data-section="header"></header>'
                + "".join(f'<section data-section="{name}"></section>' for name in
                          ("tldr", "summary", "decisions", "action-items", "open-questions", "discussion", "references"))
                + '{SVG}</body></html>')


def escape_keep_highlight(value: str) -> str:
    """모델이 쓴 인라인 강조(`<b>`, `<mark>`, `<mark class="ok|risk">`)만 살리고 그 밖의 마크업은 글자로 바꾼다."""
    escaped = _MARK_TONE_RE.sub(r'<mark class="\1">', html_lib.escape(value or "", quote=False))
    return _HIGHLIGHT_TAG_RE.sub(r"<\1\2>", escaped)


@lru_cache(maxsize=None)
def _prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def system(*names: str, design_context: str = "") -> str:
    return "\n\n".join(filter(None, [
        _prompt("page_base"), design_context, *(_prompt(n) for n in names),
    ]))


@dataclass
class VisualInputs:
    meta: Dict[str, str]
    analysis: Dict[str, Any]
    transcript: str
    terms_block: str
    research_html: str = ""
    insights_html: str = ""
    skills_dir: Path = DEFAULT_SKILLS_DIR


@dataclass
class VisualResult:
    html: str
    plan: Dict[str, Any]
    written: Dict[str, Any]
    svgs: Dict[str, str]
    usage: Dict[str, int] = field(default_factory=dict)
    fixes: int = 0


LlmFor = Callable[[str], Any]  # stage name -> chat model


# ─── model calls ────────────────────────────────────────────────────────────

def _add_usage(usage: Dict[str, int], ai: Any) -> None:
    for k, v in (getattr(ai, "usage_metadata", None) or {}).items():
        if isinstance(v, int):
            usage[k] = usage.get(k, 0) + v


def parse_json(text: str) -> Any:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in response")
    return json.loads(text[start:end + 1])


async def _call_json(llm, sys_prompt: str, user: str, usage: Dict[str, int]) -> Dict[str, Any]:
    msgs = [SystemMessage(content=sys_prompt), HumanMessage(content=user)]
    for attempt in range(2):
        ai = await astream_with_retry(llm, msgs)
        _add_usage(usage, ai)
        text = message_text(ai)
        try:
            data = parse_json(text)
            if isinstance(data, dict):
                return data
            raise ValueError("JSON is not an object")
        except ValueError as e:  # json.JSONDecodeError 도 ValueError
            if attempt == 1:
                raise
            logger.warning("[visual] unusable JSON, asking again: %s", e)
            msgs = msgs + [AIMessage(content=text), HumanMessage(content=f"JSON 파싱 오류: {e}. 올바른 JSON 하나만 다시 출력하세요.")]
    raise AssertionError("unreachable")


def _head(inp: VisualInputs) -> str:
    m = inp.meta
    return f"미팅: {m['TITLE']} · {m['DATE']} {m['START']}–{m['END']} ({m['DURATION']})\n\n{inp.terms_block}"


def _transcript(inp: VisualInputs) -> str:
    return f"### 트랜스크립트 (transcript.txt)\n```\n{inp.transcript}\n```"


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)


async def plan_page(llm, inp: VisualInputs, usage: Dict[str, int]) -> Dict[str, Any]:
    a = inp.analysis
    user = (f"{_head(inp)}\n\n### 분석 요약·주제 (analysis.json)\n```json\n{_dump({'summary': a.get('summary'), 'topics': a.get('topics')})}\n```\n\n"
            f"### 리서치 결과\n```html\n{inp.research_html or '(없음)'}\n```\n\n### 인사이트\n```html\n{inp.insights_html or '(없음)'}\n```\n\n"
            f"{_transcript(inp)}")
    design = diagram_design.planning_context(inp.skills_dir)
    return await _call_json(llm, system("page_plan", design_context=design), user, usage)


def normalize_plan(plan: Any) -> Dict[str, Any]:
    """기획 JSON 의 흔한 형식 어긋남을 고친다 — 주제 `id` 가 없으면 그림과 본문이 이어지지 않는다."""
    if not isinstance(plan, dict):
        return {}
    topics = [t for t in plan.get("topics") or [] if isinstance(t, dict)]
    seen = set()
    for i, t in enumerate(topics, start=1):
        tid = str(t.get("id") or "").strip()
        if not tid or tid in seen:
            tid = f"t{i}"
        t["id"] = tid
        seen.add(tid)
        t.setdefault("title_hint", t.get("title") or t.get("conclusion") or "")
        if not t.get("span") and t.get("start"):
            t["span"] = f"{t.get('start')}–{t.get('end', '')}"
    plan["topics"] = topics
    return plan


async def write_page(llm, inp: VisualInputs, plan: Dict[str, Any], usage: Dict[str, int]) -> Dict[str, Any]:
    user = (f"{_head(inp)}\n\n### 기획 (plan.json)\n```json\n{_dump(plan)}\n```\n\n"
            f"### 리서치 결과 (references 재료)\n```html\n{inp.research_html or '(없음)'}\n```\n\n"
            f"### 인사이트 (implications 재료)\n```html\n{inp.insights_html or '(없음)'}\n```\n\n{_transcript(inp)}")
    return await _call_json(llm, system("page_tone", "page_write"), user, usage)


def figure_jobs(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    ov = plan.get("overview") or {}
    points = "\n".join(f"{circled(p.get('n'))} {p.get('target', '')}: {'; '.join(p.get('facts') or [])}"
                       for p in ov.get("points") or [] if isinstance(p, dict))
    brief = f"개요 그림 ({ov.get('kind', '')})\n설계: {ov.get('design', '')}\n번호 지점:\n{points}"
    jobs = [{"key": key, "kind": key, "figure_kind": ov.get("kind", ""),
             "reference_kinds": ov.get("reference_kinds", []), "brief": brief}
            for key in ("overview-wide", "overview-tall")] if ov else []
    for t in plan.get("topics") or []:
        if not isinstance(t, dict) or not t.get("id"):
            continue
        fig = t.get("figure") or {}
        facts = "\n".join(f"- {f}" for f in t.get("facts") or [])
        jobs.append({"key": str(t["id"]), "kind": "topic", "figure_kind": fig.get("kind", ""),
                     "reference_kinds": fig.get("reference_kinds", []),
                     "brief": f"주제 그림 ({fig.get('kind', '')}) — 주제: {t.get('title_hint', '')}\n설계: {fig.get('design', '')}\n이 주제의 사실:\n{facts}"})
    return jobs


def extract_svgs(text: str) -> str:
    return "\n".join(re.findall(r"<svg\b.*?</svg>", text, re.S))


def svg_errors(svg: str) -> List[str]:
    return validate_html(SVG_SKELETON.replace("{SVG}", svg)) if svg else ["SVG 가 없습니다"]


async def draw_one(llm, inp: VisualInputs, job: Dict[str, Any], usage: Dict[str, int]) -> str:
    """그림 하나. 검증에 두 번 떨어지면 빈 문자열 — 페이지는 그 그림 없이 나간다."""
    user = f"{_head(inp)}\n\n### 그릴 그림: {DRAW_LABEL[job['kind']]}\nid 접두사: `{job['key']}-`\n\n{job['brief']}"
    design = diagram_design.drawing_context(inp.skills_dir, job.get("figure_kind"), job.get("reference_kinds"))
    msgs = [SystemMessage(content=system("page_draw", design_context=design)), HumanMessage(content=user)]
    for attempt in range(2):
        ai = await astream_with_retry(llm, msgs)
        _add_usage(usage, ai)
        svg = extract_svgs(message_text(ai))
        errors = svg_errors(svg)
        if not errors:
            return svg
        logger.warning("[visual] figure %s rejected (attempt %d): %s", job["key"], attempt + 1, errors[:3])
        msgs = msgs + [AIMessage(content=svg or message_text(ai)), HumanMessage(
            content="검증기가 아래 오류로 거절했습니다. 그림은 그대로 두고 오류만 고친 SVG 전체를 다시 출력하세요.\n"
                    + "\n".join(f"- {e}" for e in errors[:12]))]
    return ""


async def draw_all(llm_for: LlmFor, inp: VisualInputs, plan: Dict[str, Any], usage: Dict[str, int]) -> Dict[str, str]:
    jobs = figure_jobs(plan)
    gate = asyncio.Semaphore(MAX_PARALLEL_DRAWS)
    done = 0

    async def one(job: Dict[str, Any]) -> str:
        nonlocal done
        async with gate:
            try:
                svg = await draw_one(llm_for("draw"), inp, job, usage)
            except Exception as e:  # 그림 하나가 실패해도 페이지는 나간다
                logger.warning("[visual] figure %s failed: %s", job["key"], e)
                svg = ""
        done += 1
        events.emit(events.status(f"그림을 그렸습니다 ({done}/{len(jobs)})"))
        return svg

    svgs = await asyncio.gather(*(one(j) for j in jobs))
    return {j["key"]: s for j, s in zip(jobs, svgs)}


def walk_strings(obj: Any, path: str = ""):
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_strings(v, f"{path}.{i}" if path else str(i))


def tone_flags(written: Dict[str, Any]) -> List[str]:
    flags = []
    for path, text in walk_strings(written):
        if path.endswith((".url", ".verdict", ".id", ".n")):
            continue
        for sentence in re.split(r"(?<=[.?!])\s+", re.sub(r"<[^>]+>", "", text)):
            for pat, why in TONE_PATTERNS:
                if pat.search(sentence):
                    flags.append(f"- `{path}` ({why}): {sentence.strip()[:120]}")
    return flags


async def review_page(llm, inp: VisualInputs, plan: Dict[str, Any], written: Dict[str, Any], usage: Dict[str, int]) -> List[Dict[str, Any]]:
    flags = tone_flags(written)
    user = (f"{_head(inp)}\n\n### 자동 검사\n" + ("\n".join(flags) or "(없음)") +
            f"\n\n### 본문 (write.json)\n```json\n{_dump(written)}\n```\n\n### 기획 (plan.json)\n```json\n{_dump(plan)}\n```\n\n"
            f"### 리서치 결과\n```html\n{inp.research_html or '(없음)'}\n```\n\n{_transcript(inp)}")
    data = await _call_json(llm, system("page_tone", "page_review"), user, usage)
    fixes = data.get("fixes")
    return [f for f in fixes if isinstance(f, dict)] if isinstance(fixes, list) else []


def apply_fixes(written: Dict[str, Any], fixes: List[Dict[str, Any]]) -> int:
    """리뷰어의 `path → 고친 값` 을 문자열 위치에만 적용한다. 없는 경로는 건너뛴다."""
    applied = 0
    for fx in fixes:
        keys = str(fx.get("path", "")).split(".")
        node: Any = written
        try:
            for k in keys[:-1]:
                node = node[int(k)] if isinstance(node, list) else node[k]
            last: Any = int(keys[-1]) if isinstance(node, list) else keys[-1]
            if isinstance(node[last], str) and isinstance(fx.get("text"), str):
                node[last] = fx["text"]
                applied += 1
        except (KeyError, IndexError, ValueError, TypeError):
            logger.info("[visual] review path not found: %s", fx.get("path"))
    return applied


# ─── render ─────────────────────────────────────────────────────────────────

def t(value: Any) -> str:
    return escape_keep_highlight(PARTICLE_GAP_RE.sub(r"\1", str(value or "").strip()))


def circled(n: Any) -> str:
    try:
        return CIRCLED[int(n) - 1]
    except (ValueError, TypeError, IndexError):
        return html_lib.escape(str(n))


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _dicts(value: Any) -> List[Dict[str, Any]]:
    return [x for x in _list(value) if isinstance(x, dict)]


def ul(items: Any, cls: str = "bul") -> str:
    lis = "".join(f"<li>{t(x)}</li>" for x in _list(items) if str(x or "").strip())
    return f'<ul class="{cls}">{lis}</ul>' if lis else ""


def lead_item(x: Dict[str, Any]) -> str:
    return f'<li><b>{t(x.get("lead"))}</b> — {t(x.get("text"))}{ul(x.get("sub"), "sub")}</li>'


def sec_head(title: str, anchor: str, hint: str = "") -> str:
    hint_html = f'<p class="hint">{hint}</p>' if hint else ""
    return f'<div class="sec-head"><h2>{title}</h2><a class="anchor" href="#{anchor}" aria-label="{title} 링크">#</a>{hint_html}</div>'


def share_summary(written: Dict[str, Any]) -> str:
    text = " ".join(str(written.get(k) or "") for k in ("headline", "about"))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()[:300]


def render_page(inp: VisualInputs, w: Dict[str, Any], svgs: Dict[str, str], css: str) -> str:
    m, e = inp.meta, html_lib.escape
    ov_svg = svgs.get("overview-wide", "") + svgs.get("overview-tall", "")
    if not svgs.get("overview-tall"):  # 세로 판이 없으면 넓은 판을 모바일에서도 그대로 보여준다
        ov_svg = ov_svg.replace('class="ov-wide"', "", 1)
    decisions, actions, opens, topics_in = _dicts(w.get("decisions")), _dicts(w.get("actions")), _dicts(w.get("opens")), _dicts(w.get("topics"))

    points = "".join(f'<li><span class="num">{circled(p.get("n"))}</span><div><h4>{t(p.get("title"))}</h4>{ul(p.get("bullets"))}</div></li>'
                     for p in _dicts(w.get("points")))
    rows = lambda items: "".join(f'<li><b>{t(r.get("title"))}</b><span>{t(r.get("detail"))}</span></li>' for r in items)  # noqa: E731
    action_rows = "".join(f'<tr><td>{t(a.get("what"))}</td><td>{t(a.get("who")) or "미정"}</td><td>{t(a.get("due")) or "미정"}</td></tr>'
                          for a in actions)
    topics, toc_topics = [], []
    for i, tp in enumerate(topics_in, start=1):
        svg = svgs.get(str(tp.get("id", "")), "")
        fig = f'<figure>{svg}<figcaption>{t(tp.get("caption"))}</figcaption></figure>' if svg else ""
        paras = "".join(f'<div class="para"><h4>{t(x.get("lead"))}</h4><p>{t(x.get("text"))}</p></div>' for x in _dicts(tp.get("paras")))
        topics.append(f'<details class="topic" id="topic-{i}" open><summary><span class="n">{i}</span><span class="tt">{t(tp.get("title"))}</span></summary>'
                      f'<div class="topic-grid"><div class="paras">{paras}</div>{fig}</div></details>')
        toc_topics.append(f'<li><a href="#topic-{i}"><span>{t(tp.get("title"))}</span></a></li>')
    verdict = lambda r: VERDICT.get(r.get("verdict"), VERDICT["na"])  # noqa: E731
    refs_in = [r for r in _dicts(w.get("references")) if str(r.get("url", "")).startswith("https://")]
    refs = "".join(f'<li><a href="{e(r["url"])}">{e(str(r.get("title", "")))}</a><span class="badge {verdict(r)[0]}">{verdict(r)[1]}</span>'
                   f'<div>{t(r.get("note"))}</div></li>' for r in refs_in)
    impl = "".join(f'<div><h4>{t(x.get("title"))}</h4>{ul(x.get("bullets") or [x.get("body")])}</div>' for x in _dicts(w.get("implications")))
    glossary = "".join(f'<tr><td>{e(str(g.get("term", "")))}</td><td>{e(str(g.get("meaning", "")))}</td></tr>'
                       for g in _dicts(inp.analysis.get("terms")))
    refs_block = "".join([
        f'<h3>공식 자료 대조</h3><ul class="reflist">{refs}</ul>' if refs else "",
        f'<h3>시사점</h3><div class="impl">{impl}</div>' if impl else "",
        f'<h3>용어 사전</h3><table class="glossary"><thead><tr><th>용어</th><th>이 미팅에서의 뜻</th></tr></thead><tbody>{glossary}</tbody></table>' if glossary else "",
    ]) or '<p class="hint">참고 자료가 없습니다.</p>'
    overview = (f'<h3>전체 구조</h3><figure class="overview">{ov_svg}'
                '<ul class="legend"><li><span class="sw ok"></span>정해진 것</li><li><span class="sw new"></span>새로 추가</li>'
                '<li><span class="sw risk"></span>미확인·위험</li><li><span class="sw prop"></span>조건부 제안</li></ul>'
                f'<figcaption>{t(w.get("overview_caption"))}</figcaption></figure>') if ov_svg else ""
    toc = ('<nav class="toc" aria-label="목차"><p class="toc-title">목차</p><ol>'
           '<li><a href="#meeting">회의 개요</a></li><li><a href="#key">핵심 메시지</a></li><li><a href="#background">백그라운드</a></li>'
           '<li><a href="#summary">회의 요약</a></li>'
           '<li><a href="#outcomes">회의 결과</a><ol><li><a href="#decisions">결정 사항</a></li><li><a href="#actions">액션 아이템</a></li>'
           '<li><a href="#open">미결 사항</a></li></ol></li>'
           f'<li><a href="#flow">논의 흐름</a><ol class="toc-topics">{"".join(toc_topics)}</ol></li>'
           '<li><a href="#refs">참고 자료</a></li></ol></nav>')
    none = '<p class="hint">해당 없음</p>'
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(m['TITLE'])} · 미팅록</title>
<meta name="transmeet:meeting-id" content="{e(m['MEETING_ID'])}">
<meta name="transmeet:generated-at" content="{e(m['GENERATED_AT'])}">
<meta name="transmeet:model" content="{e(m['MODEL_ID'])}">
<meta name="transmeet:style" content="visual">
<style>
{css}
</style>
</head>
<body>
<div class="shell">
{toc}
<main class="page">
<header data-section="header" class="hero">
  <h1>{e(m['TITLE'])}</h1>
  <div class="hero-grid">
    <section class="block meeting" id="meeting"><h2 class="label">회의 개요</h2>
      <p class="about">{t(w.get('about'))}</p>
      <dl class="facts">
        <div><dt>일시</dt><dd>{e(m['DATE'])} {e(m['START'])}–{e(m['END'])}</dd></div>
        <div><dt>소요</dt><dd>{e(m['DURATION'])}</dd></div>
        <div><dt>다룬 주제</dt><dd><a href="#flow">{len(topics_in)}개</a></dd></div>
        <div><dt>회의 결과</dt><dd><a href="#decisions">결정 {len(decisions)}건</a>, <a href="#actions">액션 {len(actions)}건</a>, <a href="#open">미결 {len(opens)}건</a></dd></div>
      </dl>
    </section>
    <section class="block key" id="key" data-section="tldr"><h2 class="label">핵심 메시지</h2>
      <p class="headline">{t(w.get('headline'))}</p>
      <ul class="takeaways">{"".join(lead_item(x) for x in _dicts(w.get('tldr')))}</ul>
    </section>
  </div>
  <section class="block background" id="background"><h2 class="label">백그라운드</h2>{ul(w.get('background'), "bul cols") or none}</section>
</header>

<section class="sec" id="summary" data-section="summary">{sec_head("회의 요약", "summary")}
  <h3>주요 논의</h3>{ul(w.get('discussion'), "bul cols") or none}
  {overview}
  <ol class="points">{points}</ol>
</section>

<section class="sec" id="outcomes">{sec_head("회의 결과", "outcomes")}
  <div class="out dec" id="decisions" data-section="decisions"><h3>결정 사항 <span class="count">{len(decisions)}건</span></h3>{f'<ul class="rows">{rows(decisions)}</ul>' if decisions else none}</div>
  <div class="out act" id="actions" data-section="action-items"><h3>액션 아이템 <span class="count">{len(actions)}건</span></h3>
    {f'<table class="actions"><thead><tr><th>할 일</th><th>담당</th><th>기한</th></tr></thead><tbody>{action_rows}</tbody></table>' if actions else none}</div>
  <div class="out open" id="open" data-section="open-questions"><h3>미결 사항 <span class="count">{len(opens)}건</span></h3>{f'<ul class="rows">{rows(opens)}</ul>' if opens else none}</div>
</section>

<section class="sec" id="flow" data-section="discussion">{sec_head("논의 흐름", "flow", "주제 제목을 누르면 접고 펼칠 수 있습니다.")}
{''.join(topics) or none}
</section>

<section class="sec" id="refs">
<details class="refs" data-section="references"><summary>참고 자료 <span class="count">공식 자료 {len(refs_in)}건 대조, 시사점, 용어 사전</span></summary>
{refs_block}
</details>
</section>
</main>
</div>
</body>
</html>
"""


# ─── pipeline ───────────────────────────────────────────────────────────────

async def build_visual_page(llm_for: LlmFor, inp: VisualInputs, css: str,
                            save: Optional[Callable[[str, Any], None]] = None,
                            plan: Optional[Dict[str, Any]] = None) -> VisualResult:
    """(plan) → (write ∥ draw×N) → review → render. `save(name, obj)` 는 중간 산출물을 작업 디렉터리에 남긴다.

    그래프에서는 sources 노드가 리서치 직후 기획해 둔다. plan 이 없으면 여기서 기획한다.
    """
    save = save or (lambda name, obj: None)
    usage: Dict[str, int] = {}
    plan = normalize_plan(plan)
    if not plan:
        events.emit(events.status("페이지 구성을 기획합니다 — 주제별 사실과 그림 설계"))
        plan = normalize_plan(await plan_page(llm_for("plan"), inp, usage))
        save("visual/plan.json", plan)
    events.emit(events.status(f"본문을 쓰고 그림 {len(figure_jobs(plan))}개를 동시에 그립니다"))
    written, svgs = await asyncio.gather(write_page(llm_for("write"), inp, plan, usage), draw_all(llm_for, inp, plan, usage))
    save("visual/write.json", written)
    save("visual/svgs.json", svgs)
    events.emit(events.status("본문을 트랜스크립트와 대조해 검토합니다"))
    try:
        fixes = await review_page(llm_for("review"), inp, plan, written, usage)
    except Exception as e:  # 리뷰가 실패해도 쓴 본문으로 발행한다
        logger.warning("[visual] review failed, publishing unreviewed text: %s", e)
        fixes = []
    final = json.loads(json.dumps(written))
    applied = apply_fixes(final, fixes)
    save("visual/final.json", final)
    events.emit(events.status(f"검토에서 {applied}곳을 고쳤습니다"))
    return VisualResult(html=render_page(inp, final, svgs, css), plan=plan, written=final, svgs=svgs, usage=usage, fixes=applied)
