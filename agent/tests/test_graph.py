import json
from pathlib import Path

import pytest

from fakes import FakeChatModel, ai, tool_call
from runtime import graph as G
from runtime.publish import PublishedPage
from validator_corpus import SECTIONS

SKILLS = Path(__file__).resolve().parents[1] / "skills"

PAYLOAD = {
    "meeting": {"meetingId": "m1", "title": "주간 1:1 — 배포", "startedAt": "2026-10-06T10:00:00+09:00",
                "endedAt": "2026-10-06T10:30:00+09:00", "speakers": ["Speaker 1", "Me"], "languages": ["en", "ko"],
                "generatedAt": "2026-10-08T09:00:00+09:00", "modelId": "us.anthropic.claude-opus-5-5"},
    "messages": [
        {"timestamp": "2026-10-06T10:00:10+09:00", "speaker": "speaker1", "original": "Where are we on the pipeline?",
         "translation": "파이프라인은 어디까지 됐나요?", "detectedLanguage": "en"},
        {"timestamp": "2026-10-06T10:20:00+09:00", "speaker": "me", "original": "금요일까지 끝냅니다.", "translation": "Done by Friday.",
         "detectedLanguage": "ko"},
    ],
}


def analyze_model(*analyses):
    """analyze 단계 가짜 모델: Analysis 도구 호출로 구조화 출력을 돌려준다."""
    return FakeChatModel(responses=[ai("", [tool_call("Analysis", a.model_dump(), f"a{i}")]) for i, a in enumerate(analyses)])


def analysis(targets=1, flow=True):
    return G.Analysis(
        summary=[G.SummaryGroup(heading="배경·목적", points=["<b>배포 파이프라인 현황</b>을 점검했다."]),
                 G.SummaryGroup(heading="합의·결정", points=["<mark>금요일</mark> 마감으로 정리됐다."])],
        terms=[G.Term(term="CodeDeploy", variants=["코드 디플로이"], meaning="AWS 배포 서비스", count=3),
               G.Term(term="OIDC", variants=[], meaning="GitHub Actions 가 AWS 역할을 맡는 인증 방식", count=5)],
        topics=[G.Topic(start="10:00", end="10:15", title="파이프라인 현황", summary="진행 상황 점검."),
                G.Topic(start="10:15", end="10:30", title="일정 합의", summary="금요일 마감 합의.")],
        research_targets=[G.ResearchTarget(claim="CodeDeploy supports blue/green", said_at="10:05", query="AWS CodeDeploy blue green")][:targets],
        diagram_plan=[G.DiagramPlan(kind="timeline", title="타임라인", rationale="기본")]
        + ([G.DiagramPlan(kind="flow", title="결정 흐름", rationale="마감 결정")] if flow else []),
    )


def content_blocks(diagram_marker=""):
    """`page.html` 자리표시자를 채우는 edit_file 호출 목록 (유효한 내용)."""
    topic = ('<div class="topic" id="topic-1"><div class="topic-head"><span class="num">1</span><h3>파이프라인 현황</h3><span class="time">10:00–10:15</span></div>'
             '<h4>요지</h4><p>점검.</p><h4>핵심 발언</h4>'
             '<blockquote><span class="time">[10:00]</span>Where are we on the pipeline?</blockquote>'
             f'{diagram_marker}</div><div class="topic" id="topic-2"><div class="topic-head"><span class="num">2</span><h3>일정 합의</h3>'
             '<span class="time">10:15–10:30</span></div><h4>요지</h4><p>금요일 마감.</p></div>')
    fills = {
        "TLDR_ITEMS": "<li>금요일까지 파이프라인 완료</li><li>다음 주 알림 연결</li><li>리스크 없음</li>",
        "DECISIONS": '<li>금요일 마감 <span class="time">[10:20]</span></li>',
        "ACTION_ROWS": '<tr><td>미정</td><td>파이프라인 마무리 <span class="time">[10:20]</span></td><td>금요일</td><td><span class="badge doing">진행 중</span></td></tr>',
        "TOPIC_BLOCKS": topic,
        "OPEN_QUESTIONS": '<li class="time">해당 없음</li>',
    }
    calls = [tool_call("edit_file", {"path": "page.html", "old": "{{" + k + "}}", "new": v}, f"e-{k}") for k, v in fills.items()]
    return calls


class Recorder:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    async def __call__(self, meeting_id, html, title, summary):
        self.calls.append((meeting_id, html, title, summary))
        if self.fail:
            raise self.fail
        return PublishedPage("pid", f"https://x/p/{meeting_id}/pid.html", "t", title, summary)


def make_deps(tmp_path, models, publisher=None, **over):
    def factory(model_id, stage):
        assert model_id == "us.anthropic.claude-opus-5-5"
        try:
            return models[stage]
        except KeyError:
            raise AssertionError(f"unexpected model use in stage {stage}")
    return G.GraphDeps(llm_factory=factory, publisher=publisher or Recorder(), skills_dir=SKILLS, work_base=tmp_path, **over)


async def run(deps, payload=PAYLOAD):
    app = G.build_graph(deps)
    state = G.initial_state(payload, "s" * 33, deps)
    customs, updates = [], []
    async for mode, data in app.astream(state, stream_mode=["custom", "updates"]):
        (customs if mode == "custom" else updates).append(data)
    return customs, updates


def good_assemble_model(extra_first_rounds=()):
    responses = list(extra_first_rounds) + [
        ai("조립 시작", [tool_call("load_skill", {"name": "meeting-minutes-page"}, "l1")]),
        ai("", content_blocks("{{DIAGRAM:decision-flow.svg}}")),
        ai("", [tool_call("bash", {"command": "python3 skills/meeting-minutes-page/scripts/validate_page.py page.html"}, "b1"),
                tool_call("write_file", {"path": "page.meta.json", "content": json.dumps({"title": "주간 1:1 — 배포", "share_summary": "두 문장."})}, "w1")]),
        ai("조립을 마쳤습니다"),
    ]
    return FakeChatModel(responses=responses)


def research_model():
    return FakeChatModel(responses=[
        ai("", [tool_call("load_skill", {"name": "meeting-research"}, "l1"), tool_call("web_search", {"query": "AWS CodeDeploy blue green"}, "s1")]),
        ai("", [tool_call("write_file", {"path": "research.html", "content": '<div class="ref card"><div><a href="https://docs.aws.amazon.com/codedeploy/">CodeDeploy</a> <span class="badge ok">확인됨</span></div></div><p class="time">조회일 2026-10-09 · Web Search 1회</p>'}, "w1")]),
        ai("공식 자료 1건 확인"),
    ])


def diagrams_model():
    flow = {"title": "결정 흐름", "nodes": [{"id": "a", "label": "현황 확인", "kind": "step"}, {"id": "b", "label": "금요일 가능?", "kind": "decision"},
                                        {"id": "c", "label": "금요일 마감", "kind": "outcome"}], "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c", "label": "예"}]}
    return FakeChatModel(responses=[
        ai("", [tool_call("write_file", {"path": "diagrams/decision-flow.json", "content": json.dumps(flow, ensure_ascii=False)}, "w1")]),
        ai("", [tool_call("bash", {"command": "python3 skills/meeting-diagrams/scripts/flow_svg.py diagrams/decision-flow.json > diagrams/decision-flow.svg"}, "b1")]),
        ai("", [tool_call("write_file", {"path": "diagrams/manifest.json", "content": json.dumps([{"file": "decision-flow.svg", "kind": "flow", "caption": "결정 흐름 [10:20]", "topic": 1}])}, "w2")]),
        ai("다이어그램 1개"),
    ])


def insights_model():
    return FakeChatModel(responses=[
        ai("", [tool_call("write_file", {"path": "insights.html", "content": '<h3>리스크</h3><div class="card"><p>마감이 촉박하다.</p><p class="time">근거: <a href="#topic-2">주제 2 [10:20]</a></p></div>'}, "w1")]),
        ai("인사이트 1건"),
    ])


class FakeSearch:
    calls = 0

    async def search(self, query, max_results=5, include_domains=None):
        self.calls += 1
        return json.dumps({"results": [{"title": "CodeDeploy", "url": "https://docs.aws.amazon.com/codedeploy/"}]})


async def test_full_path_publishes_once(tmp_path):
    pub = Recorder()
    models = {"analyze": analyze_model(analysis()), "research": research_model(), "diagrams": diagrams_model(),
              "insights": insights_model(), "assemble": good_assemble_model()}
    search = FakeSearch()
    deps = make_deps(tmp_path, models, publisher=pub)
    from runtime.web_search import WebSearchClient
    deps.web_search = WebSearchClient("https://gw/mcp", "us-east-1", call_tool=lambda n, a: search.search(a["query"]))
    customs, updates = await run(deps)

    assert len(pub.calls) == 1
    meeting_id, html, title, summary = pub.calls[0]
    assert meeting_id == "m1" and title == "주간 1:1 — 배포" and summary == "두 문장."
    assert "{{" not in html
    assert html.count("<svg") == 2  # 타임라인 + 흐름도가 인라인됐다
    assert "<figcaption>결정 흐름 [10:20]</figcaption>" in html and "<figcaption>주제 타임라인</figcaption>" in html
    assert 'data-section="references"' in html and "docs.aws.amazon.com/codedeploy" in html
    assert "마감이 촉박하다" in html
    assert "2026-10-06" in html and "10:00–10:30" in html and "30분" in html  # 헤더는 코드가 채웠다
    assert "Speaker" not in html and 'class="spk"' not in html and 'class="orig"' not in html  # 화자·번역 없음
    # 코드가 채운 요약·용어 사전·목차·통계
    assert '<section data-section="summary"' in html
    assert '<h4>배경·목적</h4><ul class="points"><li><b>배포 파이프라인 현황</b>을 점검했다.</li></ul>' in html
    assert "<li><mark>금요일</mark> 마감으로 정리됐다.</li>" in html
    assert '<span class="term">OIDC</span>' in html and "코드 디플로이" in html  # 횟수 순: OIDC(5) 가 먼저
    assert html.index('<span class="term">OIDC</span>') < html.index('<span class="term">CodeDeploy</span>')
    assert '<a href="#topic-2">일정 합의<span class="time">10:15</span></a>' in html and '<a href="#sec-glossary">' in html
    assert "<li><b>2</b><span>주제</span></li>" in html and "<li><b>1</b><span>결정</span></li>" in html
    assert "<li><b>1</b><span>액션</span></li>" in html and "<li><b>0</b><span>미결</span></li>" in html and "<li><b>1</b><span>참고 자료</span></li>" in html
    # 노드 순서: research/diagrams 가 insights 앞, insights 가 assemble 앞
    order = [list(u)[0] for u in updates]
    assert order.index("research") < order.index("insights") and order.index("diagrams") < order.index("insights")
    assert order.index("insights") < order.index("assemble") < order.index("validate") < order.index("publish")
    final = {k: v for u in updates for k, v in u.items()}
    assert final["publish"]["published"]["url"].endswith("/m1/pid.html")
    phases = [c["phase"] for c in customs]
    assert "error" not in phases and phases.count("status") >= 6
    texts = [c.get("text", "") for c in customs]
    assert any("2개 주제" in t and "용어 2개" in t for t in texts) and "페이지가 발행되었습니다" in texts and any(t.startswith("웹 검색:") for t in texts)
    assert search.calls == 1
    # 작업 디렉터리 산출물
    root = Path(G.initial_state.__globals__["create_workspace"]("s" * 33, base=tmp_path, skills_dir=SKILLS).root)
    assert json.loads((root / "analysis.json").read_text())["topics"][0]["title"] == "파이프라인 현황"
    # 후속 단계 프롬프트에 용어 표기 블록이 들어갔다
    from runtime.stage import message_text
    for stage in ("research", "insights", "assemble"):
        assert "용어 표기" in message_text(models[stage].calls[0][1]) and "MACC" not in message_text(models[stage].calls[0][1])
        assert "**CodeDeploy** (녹취 표기: 코드 디플로이)" in message_text(models[stage].calls[0][1])
    assert (root / "diagrams" / "timeline.svg").exists()


async def test_empty_research_targets_skip_model(tmp_path):
    research = FakeChatModel(responses=[])
    models = {"analyze": analyze_model(analysis(targets=0, flow=False)), "research": research,
              "insights": insights_model(), "assemble": good_assemble_model()}
    pub = Recorder()
    customs, updates = await run(make_deps(tmp_path, models, publisher=pub))
    assert research.calls == []
    html = pub.calls[0][1]
    assert G.NO_RESEARCH_TEXT in html
    assert html.count("<svg") == 1  # 타임라인만
    assert "{{" not in html  # 사용되지 않은 DIAGRAM 마커는 제거됐다
    assert any("외부 사실이 없습니다" in c.get("text", "") for c in customs)


def bad_assemble_round(script_in_topic=True):
    bad = content_blocks()
    bad[3] = tool_call("edit_file", {"path": "page.html", "old": "{{TOPIC_BLOCKS}}",
                                     "new": '<div class="topic" id="topic-1"><h3>x</h3><script>alert(1)</script></div>'}, "e-bad")
    return [ai("", bad), ai("조립 끝")]


async def test_validation_failure_reassembles_then_publishes(tmp_path):
    fix = [ai("", [tool_call("edit_file", {"path": "page.html", "old": "<script>alert(1)</script>", "new": "<p>고침</p>"}, "fix")]), ai("고쳤습니다")]
    assemble = FakeChatModel(responses=bad_assemble_round() + fix)
    models = {"analyze": analyze_model(analysis(targets=0, flow=False)), "research": FakeChatModel(),
              "insights": insights_model(), "assemble": assemble}
    pub = Recorder()
    customs, updates = await run(make_deps(tmp_path, models, publisher=pub))
    assert len(pub.calls) == 1 and "<script" not in pub.calls[0][1]
    order = [list(u)[0] for u in updates]
    assert order.count("assemble") == 2 and order.count("validate") == 2
    # 두 번째 조립 프롬프트에 검증 오류가 들어갔고 page.html 은 유지됐다
    from runtime.stage import message_text
    second_prompt = message_text(assemble.calls[2][1])
    assert "검증 오류" in second_prompt and "script" in second_prompt.lower()
    assert any("검증 실패" in c.get("text", "") for c in customs)
    final = {k: v for u in updates for k, v in u.items()}
    assert final["assemble"]["assemble_attempts"] == 2


async def test_three_failures_end_in_error(tmp_path):
    rounds = bad_assemble_round() + [ai("안 고침"), ai("안 고침")]
    models = {"analyze": analyze_model(analysis(targets=0, flow=False)), "research": FakeChatModel(),
              "insights": insights_model(), "assemble": FakeChatModel(responses=rounds)}
    pub = Recorder()
    customs, updates = await run(make_deps(tmp_path, models, publisher=pub))
    assert pub.calls == []
    order = [list(u)[0] for u in updates]
    assert order.count("assemble") == 3 and order[-1] == "fail"
    last = customs[-1]
    assert last["phase"] == "error" and "script" in last["error"].lower() and last.get("transient") is False
    final = {k: v for u in updates for k, v in u.items()}
    assert final["fail"]["failed"] is True


async def test_publisher_exception_propagates(tmp_path):
    models = {"analyze": analyze_model(analysis(targets=0, flow=False)), "research": FakeChatModel(),
              "insights": insights_model(), "assemble": good_assemble_model()}
    with pytest.raises(RuntimeError, match="s3 down"):
        await run(make_deps(tmp_path, models, publisher=Recorder(fail=RuntimeError("s3 down"))))


def test_initial_state_rejects_bad_payloads(tmp_path):
    deps = make_deps(tmp_path, {})
    with pytest.raises(G.PayloadError):
        G.initial_state({"meeting": {"meetingId": "m"}, "messages": []}, "s" * 33, deps)
    with pytest.raises(G.PayloadError):
        G.initial_state({"meeting": {"meetingId": "", "modelId": "x"}, "messages": [{}]}, "s" * 33, deps)
    with pytest.raises(G.PayloadError, match="model not allowed"):
        G.initial_state({"meeting": {"meetingId": "m", "modelId": "anthropic.claude-haiku"}, "messages": [{}]}, "s" * 33, deps)
    state = G.initial_state(PAYLOAD, "s" * 33, deps)
    root = Path(state["work_dir"])
    transcript = (root / "transcript.txt").read_text()
    assert transcript.startswith("[10:00:10] Where are we on the pipeline?\n[10:20:00] 금요일까지 끝냅니다.")
    assert "Speaker" not in transcript and "파이프라인은 어디까지" not in transcript  # 화자·번역 없음
    assert json.loads((root / "meeting.json").read_text())["meetingId"] == "m1"


def test_system_prompts_exist_for_all_stages():
    for stage in ("analyze", "research", "insights", "diagrams", "assemble"):
        text = G.system_prompt(stage)
        assert "TransMeet" in text and "이 단계" in text and "원문만" in text
    assert "publish_page" not in G.system_prompt("assemble") and "code_interpreter" not in G.system_prompt("assemble")
    assert "GLOSSARY_ROWS" not in G.system_prompt("assemble") and "{{STATS}}" in G.system_prompt("assemble")
    assert "Analysis" in G.system_prompt("analyze") and "summary" in G.system_prompt("analyze")


async def test_analyze_retries_once_on_bad_structured_output(tmp_path):
    bad_then_good = FakeChatModel(responses=[ai("구조화 실패"), ai("", [tool_call("Analysis", analysis(targets=0, flow=False).model_dump(), "a1")])])
    models = {"analyze": bad_then_good, "research": FakeChatModel(), "insights": insights_model(), "assemble": good_assemble_model()}
    pub = Recorder()
    await run(make_deps(tmp_path, models, publisher=pub))
    assert len(pub.calls) == 1 and len(bad_then_good.calls) == 2
    assert "스키마" in bad_then_good.calls[1][-1].content


def test_stats_ignore_empty_placeholder_items():
    html = ('<section data-section="decisions"><ol class="list"><li class="time">결정된 사항 없음</li></ol></section>'
            '<section data-section="action-items"><table><thead><tr><th>a</th></tr></thead><tbody><tr><td colspan="4" class="time">해당 없음</td></tr></tbody></table></section>'
            '<section data-section="open-questions"><ul><li>누가 채널을 만드나 <span class="time">[10:05]</span></li><li>기한은? </li></ul></section>'
            '<section data-section="references"><p class="time">리서치 대상이 되는 외부 사실이 없었습니다.</p></section>')
    stats = G._stats_html(html)
    assert "<li><b>0</b><span>주제</span></li>" in stats and "<li><b>0</b><span>결정</span></li>" in stats
    assert "<li><b>0</b><span>액션</span></li>" in stats and "<li><b>2</b><span>미결</span></li>" in stats and "<li><b>0</b><span>참고 자료</span></li>" in stats


def test_summary_html_groups_points_and_keeps_only_highlight_tags():
    html = G._summary_html({"summary": [
        {"heading": "주요 <논의>", "points": ["<b>OIDC</b> 채택 & <mark>15분</mark> 토큰", " ", "<script>x</script> <i>no</i>"]},
        {"heading": "빈 그룹", "points": []},
    ]})
    assert html.startswith('<h4>주요 &lt;논의&gt;</h4><ul class="points">')
    assert "<li><b>OIDC</b> 채택 &amp; <mark>15분</mark> 토큰</li>" in html
    assert "<li>&lt;script&gt;x&lt;/script&gt; &lt;i&gt;no&lt;/i&gt;</li>" in html  # b·mark 외의 태그는 글자로
    assert html.count("<li>") == 2 and "빈 그룹" not in html  # 공백 항목·빈 그룹은 생략


def test_summary_html_accepts_legacy_paragraph_strings():
    html = G._summary_html({"summary": ["첫 문단.", "둘째 <b>핵심</b>."]})
    assert html == '<ul class="points"><li>첫 문단.</li><li>둘째 <b>핵심</b>.</li></ul>'


def test_glossary_and_summary_fallbacks_and_escaping():
    assert G._glossary_rows({}) == G.NO_TERMS_ROW and G._summary_html({"summary": []}) == G.NO_SUMMARY_TEXT
    assert G._summary_html({"summary": [{"heading": "x", "points": [" "]}]}) == G.NO_SUMMARY_TEXT
    rows = G._glossary_rows({"terms": [{"term": "A<B", "variants": ["x", "A<B"], "meaning": "a & b", "count": 1}]})
    assert "A&lt;B" in rows and "a &amp; b" in rows and rows.count("x") == 1  # 정식 표기와 같은 변형은 생략
    toc = G._toc_html({"topics": [{"title": "<T>", "start": "10:00"}]})
    assert '<a href="#topic-1">&lt;T&gt;<span class="time">10:00</span></a>' in toc and toc.count("<li>") == len(G.TOC_SECTIONS) + 1
