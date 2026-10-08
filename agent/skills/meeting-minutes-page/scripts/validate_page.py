#!/usr/bin/env python3
"""Validate a TransMeet meeting page before publishing.

Usage: python3 validate_page.py <page.html>
Prints JSON {"ok": bool, "errors": [...], "bytes": n}; exit 0 when ok.
The backend applies the same rules again at publish time, so a page that
fails here will also be rejected by publish_page.
"""
import json
import re
import sys
from html.parser import HTMLParser

REQUIRED_SECTIONS = [
    "header", "tldr", "decisions", "action-items", "discussion",
    "open-questions", "references", "insights", "glossary", "footer",
]
REQUIRED_META = ["transmeet:meeting-id", "transmeet:generated-at", "transmeet:model"]
MAX_BYTES = 1_500_000
_URL_ATTR_RE = re.compile(r"^(https?:)?//", re.IGNORECASE)


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections, self.meta, self.errors = set(), {}, []
        self.html_lang = None
        self._svg_depth, self._svg_has_title = 0, []
        self.style_text, self._in_style = [], False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.html_lang = a.get("lang")
        if tag == "script":
            self.errors.append("<script> is not allowed")
        if tag == "meta" and a.get("name", "").startswith("transmeet:"):
            self.meta[a["name"]] = a.get("content", "")
        if "data-section" in a:
            self.sections.add(a["data-section"])
        for name, value in attrs:
            if name.lower().startswith("on"):
                self.errors.append(f"event handler attribute not allowed: {name}")
            if value and name.lower() == "href" and value.strip().lower().startswith("javascript:"):
                self.errors.append("javascript: href not allowed")
            if value and name.lower() in ("src", "srcset", "poster", "data", "action", "formaction", "background") and _URL_ATTR_RE.match(value.strip()):
                self.errors.append(f"external resource not allowed in {tag} {name}")
        if tag == "link" and _URL_ATTR_RE.match((a.get("href") or "").strip()):
            self.errors.append("external <link> not allowed")
        if tag == "svg":
            self._svg_depth += 1
            self._svg_has_title.append(False)
        if tag == "title" and self._svg_depth > 0:
            self._svg_has_title[-1] = True
        if tag == "style":
            self._in_style = True

    def handle_endtag(self, tag):
        if tag == "svg" and self._svg_depth > 0:
            self._svg_depth -= 1
            if not self._svg_has_title.pop():
                self.errors.append("every <svg> needs a <title> for accessibility")
        if tag == "style":
            self._in_style = False

    def handle_data(self, data):
        if self._in_style:
            self.style_text.append(data)


def validate_html(html: str, max_bytes: int = MAX_BYTES) -> list:
    errors = []
    size = len(html.encode("utf-8"))
    if size > max_bytes:
        errors.append(f"page is {size} bytes, limit {max_bytes} bytes")
    if "<html" not in html.lower():
        return errors + ["not an HTML document"]
    p = _Collector()
    p.feed(html)
    p.close()
    errors.extend(p.errors)
    if p.html_lang != "ko":
        errors.append('<html lang="ko"> is required')
    for sid in REQUIRED_SECTIONS:
        if sid not in p.sections:
            errors.append(f'missing data-section="{sid}"')
    for name in REQUIRED_META:
        if not p.meta.get(name):
            errors.append(f'missing <meta name="{name}">')
    css = "\n".join(p.style_text)
    if "@import" in css:
        errors.append("@import in <style> not allowed")
    if re.search(r"url\(\s*['\"]?\s*(https?:)?//", css, re.IGNORECASE):
        errors.append("url(http...) in <style> not allowed")
    return errors


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    html = open(argv[1], encoding="utf-8").read()
    errors = validate_html(html)
    print(json.dumps({"ok": not errors, "errors": errors, "bytes": len(html.encode("utf-8"))}, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
