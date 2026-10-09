import json
import subprocess
import sys
from html import escape
from pathlib import Path

from validate_page import validate_html
from timeline_svg import render_timeline
from flow_svg import render_flow

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/meeting-diagrams/scripts"

TIMELINE = {
    "start": "10:00",
    "end": "11:00",
    "topics": [
        {"label": "킥오프 & 목표", "start": "10:00", "end": "10:12"},
        {"label": "GA 일정 논의", "start": "10:12", "end": "10:35"},
        {"label": "보안 리뷰", "start": "10:35", "end": "11:00"},
    ],
}

FLOW = {
    "title": "출시 결정 흐름",
    "nodes": [
        {"id": "a", "label": "GA 일정 확인", "kind": "step"},
        {"id": "b", "label": "5월 GA?", "kind": "decision"},
        {"id": "c", "label": "2분기 출시", "kind": "outcome"},
        {"id": "d", "label": "3분기로 연기", "kind": "outcome"},
    ],
    "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c", "label": "예"}, {"from": "b", "to": "d", "label": "아니오"}],
}


def wrap(svg: str) -> str:
    sections = "".join(
        f'<section data-section="{s}"><p>x</p></section>'
        for s in ["header", "tldr", "summary", "decisions", "action-items", "discussion", "open-questions", "references", "insights", "glossary", "footer"]
    )
    return (
        '<!doctype html><html lang="ko"><head><meta name="transmeet:meeting-id" content="m">'
        '<meta name="transmeet:generated-at" content="t"><meta name="transmeet:model" content="x"></head>'
        f"<body>{sections}{svg}</body></html>"
    )


def test_timeline_contains_labels_title_and_only_css_vars():
    svg = render_timeline(TIMELINE)
    assert svg.startswith("<svg")
    assert "<title>" in svg and "<desc>" in svg
    for t in TIMELINE["topics"]:
        assert escape(t["label"]) in svg
    assert "var(--tm-" in svg
    assert "#" not in svg.replace("&#", "")  # 색상 하드코딩 없음
    assert validate_html(wrap(svg)) == []


def test_timeline_bars_scale_with_duration():
    svg = render_timeline(TIMELINE)
    widths = [float(w) for w in __import__("re").findall(r'class="bar"[^>]*width="([\d.]+)"', svg)]
    assert len(widths) == 3
    assert widths[1] > widths[0]  # 23분 > 12분


def test_flow_renders_nodes_edges_and_labels():
    svg = render_flow(FLOW)
    for n in FLOW["nodes"]:
        assert escape(n["label"]) in svg
    assert "예" in svg and "아니오" in svg
    assert svg.count("<polygon") >= 1  # decision 노드는 다이아몬드
    assert "marker-end" in svg
    assert validate_html(wrap(svg)) == []


def test_flow_rejects_unknown_edge_target():
    bad = {**FLOW, "edges": [{"from": "a", "to": "zzz"}]}
    try:
        render_flow(bad)
    except ValueError as e:
        assert "zzz" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_cli_roundtrip(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps(TIMELINE), encoding="utf-8")
    out = subprocess.run([sys.executable, str(SCRIPTS / "timeline_svg.py"), str(p)], capture_output=True, text=True, check=True).stdout
    assert out.startswith("<svg")
    p2 = tmp_path / "f.json"
    p2.write_text(json.dumps(FLOW), encoding="utf-8")
    out2 = subprocess.run([sys.executable, str(SCRIPTS / "flow_svg.py"), str(p2)], capture_output=True, text=True, check=True).stdout
    assert out2.startswith("<svg")
