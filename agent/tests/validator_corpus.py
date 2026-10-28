"""Shared corpus for the page validator (skill script, loaded by runtime.validator for publishing).

Every BAD_PAGES entry must yield >= 1 error and every GOOD_PAGES entry 0 errors in BOTH
validator paths. Imported by agent/tests.
"""
from pathlib import Path

SECTIONS = [
    "header", "tldr", "summary", "decisions", "action-items", "discussion",
    "open-questions", "references", "insights", "glossary", "footer",
]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

_SVG = (
    '<figure><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40" role="img" font-family="var(--tm-font, sans-serif)">'
    "<title>흐름</title><desc>두 단계</desc>"
    '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
    '<path d="M0 0L10 5L0 10z" fill="var(--tm-muted)"/></marker>'
    '<linearGradient id="g"><stop offset="0" stop-color="#fff"/></linearGradient></defs>'
    '<path d="M10 20 L90 20" stroke="var(--tm-muted)" marker-end="url(#arrow)" fill="url(#g)"/>'
    '<rect x="1" y="1" width="20" height="10" rx="3"/><text x="5" y="8" text-anchor="middle">A</text>'
    "</svg><figcaption>그림 1</figcaption></figure>"
)


VISUAL_SECTIONS = ["header", "tldr", "summary", "decisions", "action-items", "open-questions", "discussion", "references"]


def page(body_extra: str = "", head_extra: str = "", css: str = "body{font-family:sans-serif}", html_attrs: str = 'lang="ko"',
         sections=SECTIONS) -> str:
    secs = "".join(f'<section data-section="{s}"><h2>{s}</h2><p>해당 없음</p></section>' for s in sections)
    return (
        f"<!doctype html><html {html_attrs}><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1"><title>미팅 · 미팅록</title>'
        '<meta name="transmeet:meeting-id" content="m1"><meta name="transmeet:generated-at" content="2026-10-08T00:00:00Z">'
        f'<meta name="transmeet:model" content="global.anthropic.claude-opus-5-5">{head_extra}<style>{css}</style></head>'
        f"<body><main>{secs}{body_extra}</main></body></html>"
    )


BAD_PAGES = {
    # Python 3.12 html.parser 는 </style x> 를 종료로 보지 않아 뒤의 <script> 가 미파싱으로 남는다
    "style_end_attr": page(body_extra='<style>a{}</style x><script>alert(1)</script>'),
    "style_end_slash": page(body_extra='<style>a{}</style/><script>alert(1)</script>'),
    # 프레젠테이션 속성의 CSS 이스케이프로 url( 우회
    "fill_css_escape": page(body_extra='<svg xmlns="http://www.w3.org/2000/svg"><title>t</title><rect fill="u\\72l(https://evil.example/x)"/></svg>'),
    "script": page("<script>alert(1)</script>"),
    "event_handler": page('<div onclick="alert(1)">x</div>'),
    "iframe_javascript_src": page('<iframe src="javascript:alert(1)"></iframe>'),
    "iframe_srcdoc": page('<iframe srcdoc="&lt;script&gt;alert(1)&lt;/script&gt;"></iframe>'),
    "srcdoc_on_allowed_tag": page('<div srcdoc="x">x</div>'),
    "form_javascript_action": page('<form action="javascript:alert(1)"><button>go</button></form>'),
    "svg_xlink_javascript": page(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><title>t</title>'
        '<a xlink:href="javascript:alert(1)"><text>x</text></a></svg>'
    ),
    "svg_animate_href": page(
        '<svg><title>t</title><a><animate attributeName="href" values="javascript:alert(1)"/><text>x</text></a></svg>'
    ),
    "entity_tab_javascript_href": page('<a href="java&#x09;script:alert(1)">x</a>'),
    "entity_colon_javascript_href": page('<a href="javascript&colon;alert(1)">x</a>'),
    "svg_style_parser_differential": page("<svg><title>t</title><style><img src=x onerror=alert(1)></style></svg>"),
    "svg_title_parser_differential": page("<svg><title><img src=x onerror=alert(1)></title></svg>"),
    "comment_parser_differential": page("<!--> <img src=x onerror=alert(1)> -->"),
    "meta_refresh": page(head_extra='<meta http-equiv="refresh" content="0;url=https://evil.example">'),
    "base_href": page(head_extra='<base href="https://evil.example/">'),
    "inline_style_url": page('<div style="background:url(https://evil.example/x.png)">x</div>'),
    "svg_image_href": page('<svg><title>t</title><image href="https://evil.example/x.png"/></svg>'),
    "svg_use_href": page('<svg><title>t</title><use href="data:image/svg+xml,&lt;svg&gt;&lt;/svg&gt;#a"/></svg>'),
    "object_data_url": page('<object data="data:text/html,&lt;script&gt;alert(1)&lt;/script&gt;"></object>'),
    "embed_data_url": page('<embed src="data:text/html,x">'),
    "css_escaped_import": page(css="@\\69mport 'https://evil.example/x.css';"),
    "css_escaped_url": page(css="body{background:\\75rl(https://evil.example/x.png)}"),
    "css_import": page(css="@import url(https://evil.example/x.css);"),
    "css_url_external": page(css="body{background:url( 'https://evil.example/x.png' )}"),
    "presentation_attr_external_url": page('<svg><title>t</title><rect fill="url(https://evil.example/#g)"/></svg>'),
    "http_link": page('<a href="http://example.com">x</a>'),
    "data_link": page('<a href="data:text/html,x">x</a>'),
    "wrong_xmlns": page('<svg xmlns="https://evil.example/ns"><title>t</title></svg>'),
    "link_stylesheet": page(head_extra='<link rel="stylesheet" href="https://evil.example/x.css">'),
    "img_external": page('<img src="https://cdn.example.com/a.png">'),
    "foreign_object": page("<svg><title>t</title><foreignObject><div>x</div></foreignObject></svg>"),
    "svg_without_title": page('<svg viewBox="0 0 1 1"><rect/></svg>'),
    "missing_section": page().replace('data-section="glossary"', 'data-section="other"'),
    "wrong_lang": page(html_attrs='lang="en"'),
    "visual_missing_discussion": page(head_extra='<meta name="transmeet:style" content="visual">',
                                      sections=[s for s in VISUAL_SECTIONS if s != "discussion"]),
    "unknown_style": page(head_extra='<meta name="transmeet:style" content="poster">'),
}

GOOD_PAGES = {
    "minimal": page(),
    "detailed_explicit": page(head_extra='<meta name="transmeet:style" content="detailed">'),
    "visual_without_footer": page(head_extra='<meta name="transmeet:style" content="visual">', sections=VISUAL_SECTIONS,
                                  body_extra='<details class="topic" open><summary>주제</summary><p><mark class="ok">합의</mark> <mark class="risk">리스크</mark></p></details>'),
    "links_and_svg": page(
        '<p><a href="https://docs.aws.amazon.com/x.html" target="_blank">문서</a> · '
        '<a href="#topic-1">주제 1</a> · <a href="mailto:team@example.com">메일</a></p>'
        '<article id="topic-1" class="topic"><h3>주제</h3><blockquote><span class="spk">Speaker 1</span> 발언</blockquote></article>'
        + _SVG
    ),
    "template_like": page(
        '<details><summary>원문</summary><p><code>a &lt; b</code> <time datetime="2026-10-08">10/8</time>'
        ' <abbr title="Continuous Integration">CI</abbr> <mark>중요</mark></p></details>'
        '<table><thead><tr><th>담당</th></tr></thead><tbody><tr><td><span class="badge ok">일치</span></td></tr></tbody></table>'
        + _SVG,
        css=":root{--tm-bg:#fff}@media (prefers-color-scheme:dark){:root{--tm-bg:#000}}details>summary{cursor:pointer}"
        "figure svg{width:100%}",
    ),
}


def e2e_sample_page() -> str:
    return (FIXTURES / "e2e-sample-page.html").read_text(encoding="utf-8")
