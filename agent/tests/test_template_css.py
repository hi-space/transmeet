"""Regression guards for the shared page CSS (assets/page.css) that caused mobile horizontal scroll.

`.time` doubles as a "small muted mono" style on block elements (`<p class="time">근거: …</p>`,
`<td class="time">`, `<li class="time">`). `white-space:nowrap` must therefore apply only to the
inline chip usage (`<span class="time">[HH:MM]</span>`), otherwise a long 근거 line in the insights
section pushes the whole page wider than a phone viewport.
"""
import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "skills/meeting-minutes-page/assets"
TEMPLATE = ASSETS / "page.css"  # 세 템플릿이 공유하는 CSS


def _rules(css: str, selector: str) -> list[str]:
    """Declaration blocks whose selector list is exactly `selector`."""
    return [m.group(1) for m in re.finditer(r"(?:^|[}\s])" + re.escape(selector) + r"\{([^}]*)\}", css)]


def test_time_class_does_not_force_nowrap_on_block_elements():
    css = TEMPLATE.read_text(encoding="utf-8")
    time_rules = _rules(css, ".time")
    assert time_rules, "template must still define a bare `.time` rule"
    assert not any("nowrap" in r for r in time_rules), (
        "`.time` must not be nowrap — <p class=\"time\">근거: …</p> would overflow on mobile"
    )


def test_time_chip_span_stays_on_one_line():
    css = TEMPLATE.read_text(encoding="utf-8")
    assert any("nowrap" in r for r in _rules(css, "span.time")), (
        "`span.time` should keep the [HH:MM] chip unbroken"
    )


import pytest  # noqa: E402
from runtime.validator import validate_html  # noqa: E402


def test_detailed_template_passes_validator_once_filled():
    """CSS(백슬래시·url() 금지)와 템플릿 마크업이 검증기를 통과해야 모델이 고칠 수 없는 오류가 생기지 않는다."""
    html = (ASSETS / "template.html").read_text(encoding="utf-8").replace("{{CSS}}", TEMPLATE.read_text(encoding="utf-8"))
    html = re.sub(r"\{\{[A-Z_]+\}\}", "x", html)
    assert validate_html(html) == []
    assert 'name="transmeet:style"' in html


def test_visual_css_has_no_forbidden_constructs_and_hides_tall_overview_on_desktop():
    css = (ASSETS / "visual.css").read_text(encoding="utf-8")
    assert "\\" not in css and "url(" not in css
    assert ".ov-tall{display:none}" in css and "word-break:keep-all" in css
