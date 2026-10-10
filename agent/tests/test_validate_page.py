import json
import subprocess
import sys
from pathlib import Path

from validate_page import REQUIRED_SECTIONS, validate_html

SCRIPT = Path(__file__).resolve().parents[1] / "skills/meeting-minutes-page/scripts/validate_page.py"


def minimal_page(**overrides) -> str:
    sections = "\n".join(
        f'<section data-section="{sid}"><h2>{sid}</h2><p>해당 없음</p></section>'
        for sid in REQUIRED_SECTIONS
    )
    meta = overrides.get(
        "meta",
        '<meta name="transmeet:meeting-id" content="m1">'
        '<meta name="transmeet:generated-at" content="2026-10-08T00:00:00Z">'
        '<meta name="transmeet:model" content="global.anthropic.claude-opus-5-5">',
    )
    body_extra = overrides.get("body_extra", "")
    lang = overrides.get("lang", "ko")
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">{meta}'
        f"<style>body{{font-family:sans-serif}}</style></head><body>{sections}{body_extra}</body></html>"
    )


def test_validate_accepts_sections_with_placeholder_text():
    assert validate_html(minimal_page()) == []


def test_missing_section_reported():
    html = minimal_page().replace('data-section="glossary"', 'data-section="other"')
    errors = validate_html(html)
    assert any("glossary" in e for e in errors)


def test_rejects_script_and_event_handlers():
    html = minimal_page(body_extra='<script>alert(1)</script><img src="x" onerror="alert(1)">')
    errors = validate_html(html)
    assert any("script" in e.lower() for e in errors)
    assert any("onerror" in e for e in errors)


def test_rejects_external_resources_but_allows_links_and_xmlns():
    ok = minimal_page(
        body_extra='<a href="https://docs.aws.amazon.com/x.html">문서</a>'
        '<svg xmlns="http://www.w3.org/2000/svg"><title>t</title></svg>'
    )
    assert validate_html(ok) == []
    bad = minimal_page(body_extra='<img src="https://cdn.example.com/a.png">')
    assert any("src" in e for e in validate_html(bad))
    bad_css = minimal_page(body_extra="<style>@import url(https://x/y.css);</style>")
    assert any("@import" in e or "url(" in e for e in validate_html(bad_css))


def test_requires_lang_and_meta():
    assert any("lang" in e for e in validate_html(minimal_page(lang="en")))
    assert any("transmeet:model" in e for e in validate_html(minimal_page(meta="")))


def test_svg_requires_title():
    html = minimal_page(body_extra="<svg viewBox='0 0 10 10'><rect/></svg>")
    assert any("<title>" in e for e in validate_html(html))


def test_size_limit():
    html = minimal_page(body_extra="<p>" + "가" * 10 + "</p>")
    assert any("bytes" in e for e in validate_html(html, max_bytes=100))


def test_cli_exit_code(tmp_path):
    good = tmp_path / "good.html"
    good.write_text(minimal_page(), encoding="utf-8")
    res = subprocess.run([sys.executable, str(SCRIPT), str(good)], capture_output=True, text=True)
    assert res.returncode == 0
    assert json.loads(res.stdout)["ok"] is True
    bad = tmp_path / "bad.html"
    bad.write_text("<html></html>", encoding="utf-8")
    res = subprocess.run([sys.executable, str(SCRIPT), str(bad)], capture_output=True, text=True)
    assert res.returncode == 1
    assert json.loads(res.stdout)["ok"] is False


# ─── Shared corpus: the skill script and the runtime wrapper must agree ──

import pytest  # noqa: E402
from runtime.validator import validate_html as backend_validate_html  # noqa: E402
from validator_corpus import BAD_PAGES, GOOD_PAGES, e2e_sample_page  # noqa: E402


@pytest.mark.parametrize("name", sorted(BAD_PAGES))
def test_corpus_bad_pages_rejected_identically(name):
    errs = validate_html(BAD_PAGES[name])
    assert errs, f"{name} passed the skill validator"
    assert backend_validate_html(BAD_PAGES[name], 1_500_000) == errs


@pytest.mark.parametrize("name", sorted(GOOD_PAGES))
def test_corpus_good_pages_accepted_identically(name):
    assert validate_html(GOOD_PAGES[name]) == []
    assert backend_validate_html(GOOD_PAGES[name], 1_500_000) == []


def test_real_e2e_page_passes_both():
    html = e2e_sample_page()
    assert validate_html(html) == [] and backend_validate_html(html, 1_500_000) == []


def test_cli_rejects_bypass_with_json_errors(tmp_path):
    bad = tmp_path / "bad.html"
    bad.write_text(BAD_PAGES["entity_tab_javascript_href"], encoding="utf-8")
    res = subprocess.run([sys.executable, str(SCRIPT), str(bad)], capture_output=True, text=True)
    out = json.loads(res.stdout)
    assert res.returncode == 1 and out["ok"] is False and any("javascript" in e for e in out["errors"])


def test_runtime_validator_wraps_skill_script():
    from runtime import validator
    assert validator.REQUIRED_SECTIONS == REQUIRED_SECTIONS
    assert validator.validate_html("<p>x</p>", 1_500_000) == ["not an HTML document"]
    assert validator.validate_html("<html lang=\"ko\"></html>", 3) and "limit 3 bytes" in validator.validate_html("<html lang=\"ko\"></html>", 3)[0]
