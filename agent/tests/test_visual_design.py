"""Exercise skill routing at the planner/drawer request boundary."""
import json
import shutil
from pathlib import Path

import pytest

from fakes import FakeChatModel, ai
from runtime import visual


SKILLS = Path(__file__).resolve().parents[1] / "skills"
SVG = (
    '<svg viewBox="0 0 560 240" role="img" aria-labelledby="fixture-title fixture-desc">'
    '<title id="fixture-title">센서 위치</title>'
    '<desc id="fixture-desc">이동 로봇의 몸체와 센서 장착 위치를 보여줍니다.</desc>'
    '<circle cx="280" cy="120" r="48" fill="var(--panel)"/></svg>'
)


@pytest.fixture
def skill_bundle(tmp_path):
    """A deployment-specific bundle proves we do not read a plugin-cache path."""
    root = tmp_path / "skills"
    source = SKILLS / "aws-diagram-design"
    shutil.copytree(source, root / source.name)
    refs = root / "aws-diagram-design/references"
    for name, marker in (
        ("type-sequence.md", "DEPLOYED_SEQUENCE_GUIDANCE"),
        ("type-flowchart.md", "DEPLOYED_FLOWCHART_GUIDANCE"),
        ("type-process.md", "DEPLOYED_PROCESS_GUIDANCE"),
        ("type-gantt.md", "DEPLOYED_GANTT_GUIDANCE"),
        ("technical-illustration.md", "DEPLOYED_ILLUSTRATION_GUIDANCE"),
        ("freeform.md", "DEPLOYED_FREEFORM_GUIDANCE"),
    ):
        path = refs / name
        existing = path.read_text() if path.exists() else ""
        path.write_text(existing + "\n" + marker + "\n")
    return root


def inputs(skill_bundle):
    return visual.VisualInputs(
        meta={"TITLE": "로봇 동작 검토", "DATE": "2026-10-28",
              "START": "10:00", "END": "10:30", "DURATION": "30분"},
        analysis={}, transcript="전방 센서가 장애물을 감지하면 정지합니다.",
        terms_block="", skills_dir=skill_bundle,
    )


async def test_planner_receives_the_deployed_skill_catalog(skill_bundle):
    model = FakeChatModel(responses=[ai(json.dumps({"topics": []}))])

    assert await visual.plan_page(model, inputs(skill_bundle), {}) == {"topics": []}

    system = model.calls[0][0].content
    assert "aws-diagram-design" in system
    assert "DEPLOYED_ILLUSTRATION_GUIDANCE" in system
    assert "DEPLOYED_SEQUENCE_GUIDANCE" not in system  # selected later by draw
    assert "type-sequence.md" in system
    assert "type-gantt.md" in system


async def test_freeform_drawing_can_combine_reference_types(skill_bundle):
    plan = {
        "topics": [{"id": "t1", "title_hint": "센서와 동작", "facts": [],
                    "figure": {"kind": "freeform", "reference_kinds": ["coverage", "sequence", "coverage"],
                               "design": "센서 배치와 동작을 하나의 장면으로 설명"}}],
    }
    model = FakeChatModel(responses=[ai(SVG)])
    job = visual.figure_jobs(plan)[0]

    assert await visual.draw_one(model, inputs(skill_bundle), job, {}) == SVG
    system = model.calls[0][0].content
    assert "DEPLOYED_FREEFORM_GUIDANCE" in system
    assert system.count("DEPLOYED_ILLUSTRATION_GUIDANCE") == 1
    assert system.count("DEPLOYED_SEQUENCE_GUIDANCE") == 1


@pytest.mark.parametrize("illustration_kind", [
    "annotated", "coverage", "cutaway", "exploded", "motion", "storyboard",
])
async def test_each_drawing_uses_its_planned_type_and_keeps_page_contract(skill_bundle, illustration_kind):
    plan = {
        "overview": {"kind": illustration_kind, "design": "전방 감지 영역", "points": []},
        "topics": [{"id": "t1", "title_hint": "메시지 전달", "facts": [],
                    "figure": {"kind": "sequence", "design": "센서에서 제어기로"}}],
    }
    jobs = visual.figure_jobs(plan)
    assert [job["key"] for job in jobs] == ["overview-wide", "overview-tall", "t1"]
    for job in jobs:
        model = FakeChatModel(responses=[ai(SVG)])
        assert await visual.draw_one(model, inputs(skill_bundle), job, {}) == SVG
        system = model.calls[0][0].content
        expected, absent = (
            ("DEPLOYED_SEQUENCE_GUIDANCE", "DEPLOYED_ILLUSTRATION_GUIDANCE")
            if job["key"] == "t1" else
            ("DEPLOYED_ILLUSTRATION_GUIDANCE", "DEPLOYED_SEQUENCE_GUIDANCE")
        )
        assert expected in system
        assert absent not in system
        # Skill examples must be subordinate to the existing SVG output contract.
        assert system.index(expected) < system.index("### 기술 제약")
        assert model.calls[0][-1].content.endswith(job["brief"])
        assert not visual.svg_errors(SVG)


@pytest.mark.parametrize(("kind", "marker"), [
    ("flow", "DEPLOYED_FLOWCHART_GUIDANCE"),
    ("steps", "DEPLOYED_PROCESS_GUIDANCE"),
    ("schedule", "DEPLOYED_GANTT_GUIDANCE"),
    (" Sequence ", "DEPLOYED_SEQUENCE_GUIDANCE"),
])
async def test_legacy_figure_kinds_select_a_reference(skill_bundle, kind, marker):
    model = FakeChatModel(responses=[ai(SVG)])
    job = {"key": "t1", "kind": "topic", "figure_kind": kind, "brief": "그림"}

    assert await visual.draw_one(model, inputs(skill_bundle), job, {}) == SVG
    assert marker in model.calls[0][0].content


@pytest.mark.parametrize("kind", [
    None, "", "../../private", ["sequence"], {"kind": "sequence"}, "unknown-type",
])
async def test_unrecognized_kind_uses_common_guidance_without_reading_a_path(skill_bundle, kind):
    (skill_bundle / "private.md").write_text("MUST_NOT_REACH_THE_MODEL")
    model = FakeChatModel(responses=[ai(SVG)])
    job = {"key": "t1", "kind": "topic", "figure_kind": kind, "brief": "그림"}

    assert await visual.draw_one(model, inputs(skill_bundle), job, {}) == SVG
    system = model.calls[0][0].content
    assert "aws-diagram-design" in system
    assert "DEPLOYED_FREEFORM_GUIDANCE" in system
    assert "MUST_NOT_REACH_THE_MODEL" not in system
    assert "DEPLOYED_SEQUENCE_GUIDANCE" not in system


@pytest.mark.parametrize("references", ["sequence", {"kind": "sequence"}, ["../../private", None]])
async def test_reference_hints_cannot_supply_file_paths(skill_bundle, references):
    model = FakeChatModel(responses=[ai(SVG)])
    job = {"key": "t1", "kind": "topic", "figure_kind": "freeform",
           "reference_kinds": references, "brief": "자유롭게 설명"}

    assert await visual.draw_one(model, inputs(skill_bundle), job, {}) == SVG
    system = model.calls[0][0].content
    assert "DEPLOYED_FREEFORM_GUIDANCE" in system
    assert "DEPLOYED_SEQUENCE_GUIDANCE" not in system
