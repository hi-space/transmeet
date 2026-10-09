"""Regression guards for template.html CSS that caused mobile horizontal scroll.

`.time` doubles as a "small muted mono" style on block elements (`<p class="time">근거: …</p>`,
`<td class="time">`, `<li class="time">`). `white-space:nowrap` must therefore apply only to the
inline chip usage (`<span class="time">[HH:MM]</span>`), otherwise a long 근거 line in the insights
section pushes the whole page wider than a phone viewport.
"""
import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "skills/meeting-minutes-page/assets/template.html"


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
