#!/usr/bin/env python3
"""Validate a TransMeet meeting page before publishing.

Usage: python3 validate_page.py <page.html>
Prints JSON {"ok": bool, "errors": [...], "bytes": n}; exit 0 when ok.
The backend applies the same rules again at publish time, so a page that
fails here will also be rejected by publish_page.
"""
import html as html_lib
import json
import re
import sys
from html.parser import HTMLParser
from typing import Dict, List, Optional

MAX_BYTES = 1_500_000

# ── validator core: keep identical in backend/app/page_agent.py and agent/skills/meeting-minutes-page/scripts/validate_page.py ──
REQUIRED_SECTIONS = [
    "header", "tldr", "decisions", "action-items", "discussion",
    "open-questions", "references", "insights", "glossary", "footer",
]
REQUIRED_META = ["transmeet:meeting-id", "transmeet:generated-at", "transmeet:model"]

# 허용 목록 방식: 여기에 없는 태그는 전부 거절한다 (script·iframe·object·embed·form·img·use·animate 등).
ALLOWED_HTML_TAGS = {
    "html", "head", "body", "meta", "title", "style", "main", "header", "footer", "section", "article", "nav",
    "aside", "h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "span", "ul", "ol", "li", "dl", "dt", "dd",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "blockquote", "a", "b", "strong", "i",
    "em", "code", "pre", "br", "hr", "small", "sup", "sub", "details", "summary", "figure", "figcaption",
    "time", "abbr", "mark",
}
# HTMLParser 는 태그 이름을 소문자로 넘긴다 (linearGradient → lineargradient).
ALLOWED_SVG_TAGS = {
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "title",
    "desc", "defs", "marker", "lineargradient", "stop", "clippath",
}
ALLOWED_TAGS = ALLOWED_HTML_TAGS | ALLOWED_SVG_TAGS
ALLOWED_META_ATTRS = {"charset", "name", "content"}
FORBIDDEN_ATTRS = {"srcdoc", "formaction", "ping"}
URL_ATTRS = {
    "href", "xlink:href", "src", "srcset", "action", "data", "poster", "background", "cite", "longdesc",
    "usemap", "codebase", "values", "from", "to", "by",
}
NAMESPACE_ATTRS = {
    "xmlns": {"http://www.w3.org/2000/svg", "http://www.w3.org/1999/xhtml"},
    "xmlns:xlink": {"http://www.w3.org/1999/xlink"},
}
SAFE_URL_SCHEMES = {"https", "mailto"}
LINK_ATTRS = {"href", "xlink:href", "cite"}
_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.-]*):")
_DANGEROUS_VALUE_RE = re.compile(r"^(javascript|vbscript|data|http):")
_UNSAFE_URL_FN_RE = re.compile(r"url\((?!['\"]?#)")
_CTRL_WS_RE = re.compile(r"[\x00-\x20\x7f]+")
_RAW_MARKUP_RE = re.compile(r"<[a-zA-Z/!?]")


def _normalize_attr(value: str) -> str:
    """브라우저가 URL 을 해석하는 방식에 맞춰 엔티티를 풀고 제어문자·공백을 지운 뒤 소문자로 만든다."""
    return _CTRL_WS_RE.sub("", html_lib.unescape(value)).lower()


class _HtmlAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: List[str] = []
        self.sections: set = set()
        self.meta: Dict[str, str] = {}
        self.lang: Optional[str] = None
        self._svg_titles: List[bool] = []
        self._in_style = False
        self.css: List[str] = []

    def _error(self, message: str) -> None:
        if message not in self.errors:
            self.errors.append(message)

    def _check_attr(self, tag: str, name: str, value: Optional[str]) -> None:
        if name.startswith("on"):
            self._error(f"event handler attribute not allowed: {name}")
            return
        if name in FORBIDDEN_ATTRS or (name.startswith("xmlns") and name not in NAMESPACE_ATTRS):
            self._error(f"attribute not allowed: {name}")
            return
        if tag == "meta" and name not in ALLOWED_META_ATTRS:
            self._error(f"<meta {name}> is not allowed")
            return
        if value is None:
            return
        if name in NAMESPACE_ATTRS:
            if value.strip() not in NAMESPACE_ATTRS[name]:
                self._error(f"namespace must be the W3C URL in <{tag} {name}>")
            return
        norm = _normalize_attr(value)
        if name == "style":
            raw = html_lib.unescape(value).lower()
            if "url(" in norm or "@import" in norm or "expression" in norm or "image-set(" in norm or "\\" in raw:
                self._error(f"unsafe inline style in <{tag}>")
            return
        if name in URL_ATTRS:
            m = _SCHEME_RE.match(norm)
            if name in LINK_ATTRS:
                # 링크는 https·mailto·#fragment·상대 경로만
                if m and m.group(1) not in SAFE_URL_SCHEMES:
                    self._error(f"URL scheme not allowed in <{tag} {name}>: {m.group(1)}:")
                elif not m and norm.startswith("//"):
                    self._error(f"protocol-relative URL not allowed in <{tag} {name}>")
            elif m or norm.startswith("//"):
                self._error(f"external resource not allowed in <{tag} {name}>")
            return
        if _DANGEROUS_VALUE_RE.match(norm) or _UNSAFE_URL_FN_RE.search(norm):
            self._error(f"unsafe value in <{tag} {name}>")

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in ALLOWED_TAGS:
            self._error(f"<{tag}> is not allowed")
        if tag == "html":
            self.lang = a.get("lang")
        if tag == "meta" and (a.get("name") or "").startswith("transmeet:"):
            self.meta[a["name"]] = a.get("content") or ""
        if "data-section" in a:
            self.sections.add(a["data-section"])
        for name, value in attrs:
            self._check_attr(tag, name.lower(), value)
        if tag == "style" and self._svg_titles:
            # SVG 안의 <style> 은 브라우저에서 raw text 가 아니라 파서 차이로 태그가 숨어들 수 있다.
            self._error("<style> inside <svg> is not allowed")
        if tag == "svg":
            self._svg_titles.append(False)
        if tag == "title" and self._svg_titles:
            self._svg_titles[-1] = True
        if tag == "style":
            self._in_style = True

    def handle_endtag(self, tag):
        if tag == "svg" and self._svg_titles and not self._svg_titles.pop():
            self._error("every <svg> needs a <title> for accessibility")
        if tag == "style":
            self._in_style = False

    def handle_data(self, data):
        # <style>/<title> 등 raw text 안의 태그 모양 문자열: 브라우저(특히 SVG 안)에서는 실제 태그가 될 수 있다.
        if self.cdata_elem and _RAW_MARKUP_RE.search(data):
            self._error(f"markup inside <{self.cdata_elem}> is not allowed")
        if self._in_style:
            self.css.append(data)

    def handle_comment(self, data):
        if "<" in data or ">" in data:
            self._error("comment containing < or > is not allowed")

    def handle_decl(self, decl):
        if not decl.lower().startswith("doctype"):
            self._error(f"declaration not allowed: <!{decl[:20]}>")

    def unknown_decl(self, data):
        self._error("CDATA section is not allowed")

    def handle_pi(self, data):
        self._error("processing instruction is not allowed")


def _check_css(css: str) -> List[str]:
    errors: List[str] = []
    low = css.lower()
    if "@import" in low:
        errors.append("@import in <style> not allowed")
    if _UNSAFE_URL_FN_RE.search(re.sub(r"\s+", "", low)):
        errors.append("url() in <style> not allowed (only url(#id))")
    if "image-set(" in low or "expression(" in low:
        errors.append("image-set()/expression() in <style> not allowed")
    if "\\" in css:
        errors.append("CSS escape (backslash) in <style> not allowed")
    return errors


def _validate(html: str, max_bytes: int) -> List[str]:
    errors: List[str] = []
    size = len(html.encode("utf-8"))
    if size > max_bytes:
        errors.append(f"page is {size} bytes, limit {max_bytes} bytes")
    if "<html" not in html.lower():
        return errors + ["not an HTML document"]
    audit = _HtmlAudit()
    audit.feed(html)
    audit.close()
    errors.extend(audit.errors)
    if audit.lang != "ko":
        errors.append('<html lang="ko"> is required')
    errors.extend(f'missing data-section="{s}"' for s in REQUIRED_SECTIONS if s not in audit.sections)
    errors.extend(f'missing <meta name="{m}">' for m in REQUIRED_META if not audit.meta.get(m))
    errors.extend(_check_css("\n".join(audit.css)))
    return errors
# ── end validator core ──


def validate_html(html: str, max_bytes: int = MAX_BYTES) -> list:
    return _validate(html, max_bytes)


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
