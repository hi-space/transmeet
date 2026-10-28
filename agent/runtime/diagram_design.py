"""Load the bundled AWS design skill for the tool-free visual page stages.

Only trusted, bundled reference names become paths. The model's figure kind
selects a reference; it never supplies a filename.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path


SKILL_NAME = "aws-diagram-design"
UPSTREAM_TYPES = (
    "architecture", "it-state", "flowchart", "sequence", "state", "er",
    "timeline", "swimlane", "quadrant", "radar", "loop", "nested", "tree",
    "org-chart", "layers", "venn", "pyramid", "bar", "line", "gantt",
    "scatter", "high-level", "process", "medallion", "data-flow",
    "dp-integration", "dp-security-matrix",
)
ILLUSTRATION_TYPES = ("annotated", "coverage", "cutaway", "exploded", "motion", "storyboard")
TYPE_REFERENCES = {
    **{name: f"type-{name}.md" for name in UPSTREAM_TYPES},
    **{name: "technical-illustration.md" for name in ILLUSTRATION_TYPES},
    "compare": "comparison.md",
    "matrix": "comparison.md",
}
ALIASES = {"flow": "flowchart", "steps": "process", "schedule": "gantt"}
SEMANTIC_TYPES = {"architecture", "data-flow", "flowchart", "layers", "process"}


@lru_cache(maxsize=256)
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _sections(root: Path, numbers: tuple[int, ...]) -> str:
    """Extract numbered sections without changing the pinned upstream file."""
    body = _read(root / "SKILL.md")
    result = []
    for section in re.split(r"(?m)(?=^## \d+\. )", body):
        heading = re.match(r"## (\d+)\. ", section)
        if heading and int(heading[1]) in numbers:
            # Interactive confirmation is handled by the app, not this model call.
            result.append(section.split("### Confirm before drawing", 1)[0].strip())
    return "\n\n".join(result)


def _reference(root: Path, name: str) -> str:
    return f"### 스킬 참조: {name}\n\n{_read(root / 'references' / name)}"


def _context(root: Path, parts: list[str]) -> str:
    # The adaptation follows the original examples so its page contract wins.
    return "\n\n".join([
        f"## 적용 스킬: {SKILL_NAME}\n아래는 실행 코드가 로드한 스킬 본문과 참조 문서입니다.",
        *parts,
        _reference(root, "transmeet.md"),
    ])


def planning_context(skills_dir: Path) -> str:
    root = skills_dir / SKILL_NAME
    return _context(root, [
        _sections(root, (1, 2, 3)),
        "### figure.kind 코드\n" + ", ".join(TYPE_REFERENCES),
        _reference(root, "technical-illustration.md"),
        _reference(root, "comparison.md"),
    ])


def drawing_context(skills_dir: Path, kind: object) -> str:
    root = skills_dir / SKILL_NAME
    name = kind.strip().lower().replace("_", "-") if isinstance(kind, str) else ""
    name = ALIASES.get(name, name)
    parts = [_sections(root, (1, 4, 6, 7))]
    reference = TYPE_REFERENCES.get(name)
    if reference:
        parts.append(_reference(root, reference))
    if name in SEMANTIC_TYPES:
        parts.append(_reference(root, "semantic-patterns.md"))
    if name in ILLUSTRATION_TYPES:
        parts.append(_reference(root, "primitive-annotation.md"))
    return _context(root, parts)
