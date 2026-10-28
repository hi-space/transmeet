"""Check that formatted page copy preserves content and drops active markup.

Uses the real clipboard normalizer with an in-memory browser DOM.
Run: python tests/clipboard_content_browser.py
"""

import asyncio
import json
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("line break", "<p>Before<br>After</p>", "Before\nAfter"),
    ("quoted original", '<blockquote><span class="spk">Alice</span>Translation'
     '<span class="orig">Original</span></blockquote>', "Alice Translation\nOriginal"),
    ("literal emphasis", "<p>Type **literal** and _draft_ exactly.</p>",
     "Type **literal** and _draft_ exactly."),
    ("literal link", "<p>Write [x](y) literally.</p>", "Write [x](y) literally."),
    ("inline backticks", "<p><code>echo `date`</code></p>", "echo `date`"),
    ("fenced code", "<pre><code>```js\nconst x = 1;\n```</code></pre>",
     "```js\nconst x = 1;\n```"),
    ("indented code", "<pre><code>    x = 1\n</code></pre>", "    x = 1"),
    ("literal list", "<p>1. Not a list<br>* Not a list</p>", "1. Not a list\n* Not a list"),
    ("heading text", "<h2>Chapter [one] *literal*</h2>", "Chapter [one] *literal*"),
    ("pipe in table code", "<table><tr><th>Value</th></tr>"
     "<tr><td><code>left|right</code></td></tr></table>", "• Value: left|right"),
    ("padded inline code", "<p><code>  x  </code></p>", "  x  "),
    ("link parentheses", '<p><a href="https://example.com/a)b">Document</a></p>',
     "Document (https://example.com/a)b)"),
    ("code whitespace", "<pre>first  \n\n\n    second\n</pre>", "first  \n\n\n    second"),
    ("relative link", '<p><a href="../guide">Guide</a></p>',
     "Guide (https://example.com/p/guide)"),
    ("active markup", '<script>alert(1)</script><p style="color:red" onclick="alert(1)">'
     '<a href="javascript:alert(1)">Unsafe link</a></p>', "Unsafe link"),
    ("table caption", "<table><caption>All amounts in USD; tax excluded</caption>"
     "<tr><th>Price</th></tr><tr><td>20</td></tr></table>",
     "All amounts in USD; tax excluded\n\n• Price: 20"),
    ("nested list", "<ul><li>Parent<ul><li>Child</li></ul></li></ul>",
     "• Parent\n  • Child"),
    ("definition list", "<dl><dt>Can we launch?</dt><dd>Only after review.</dd></dl>",
     "• Can we launch?\n  Only after review."),
    ("page metadata", '<dl class="meta"><div><dt>일시</dt><dd>2026-10-28 10:00–11:00</dd>'
     '</div><div><dt>소요</dt><dd>60분</dd></div></dl>',
     "• 일시: 2026-10-28 10:00–11:00\n• 소요: 60분"),
]


def node(script, data=None):
    return subprocess.check_output(
        ["node", "--input-type=module", "-e", script],
        input=data, text=True, cwd=ROOT,
    )


async def main():
    converters = node("""
      import fs from 'node:fs';
      import ts from 'typescript';
      console.log(['src/lib/clipboardContent.ts']
        .map(file => ts.transpileModule(fs.readFileSync(file, 'utf8'), {
          compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext}
        }).outputText.replace(/^export (?=function|class|const)/gm, ''))
        .join('\\n'));
    """)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.evaluate(converters + """
          window.toClipboard = createPageClipboardContent;
        """)
        failures = []
        for name, html, expected in CASES:
            copied = await page.evaluate("""html => {
              const div = document.createElement('div');
              const result = window.toClipboard(html, 'https://example.com/p/demo/');
              div.innerHTML = result.html;
              document.body.replaceChildren(div);
              return {...result, visible: div.innerText};
            }""", html)
            if copied["text"] != expected:
                failures.append({"case": name, "expected": expected, "actual": copied["text"]})
            if name in ("line break", "quoted original") and "\n" not in copied["visible"]:
                failures.append({"case": name, "visible": copied["visible"]})
            if name == "link parentheses" and 'href="https://example.com/a)b"' not in copied["html"]:
                failures.append({"case": name, "html": copied["html"]})
            if name == "active markup" and any(
                token in copied["html"] for token in ["script", "style=", "onclick=", "javascript:"]
            ):
                failures.append({"case": name, "html": copied["html"]})
        await browser.close()
        assert not failures, json.dumps(failures, ensure_ascii=False, indent=2)
        print(json.dumps({"passed": len(CASES), "failures": 0}))


if __name__ == "__main__":
    asyncio.run(main())
