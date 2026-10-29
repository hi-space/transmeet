"""Build one portable HTML guide with embedded CSS, JavaScript and screenshots."""

import base64
from html import escape
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DESTINATION = ROOT / "public/guide/index.html"


def build():
    manifest = json.loads((HERE / "screenshots/manifest.json").read_text())
    used = set()

    def screenshot(match):
        name = match.group(1)
        item = manifest["screenshots"][name]
        used.add(name)
        data = base64.b64encode((HERE / "screenshots" / item["file"]).read_bytes()).decode()
        aspect = item["width"] / item["height"]
        size = "landscape" if aspect > 1.45 else "portrait"
        if item["height"] < 250:
            size = "strip"
        pins = ""
        legend = ""
        for pin in item["pins"]:
            rect = pin["rect"]
            style = ";".join(f"{key}:{value}%" for key, value in rect.items())
            pins += (
                f'<span class="annotation" style="{style}" aria-hidden="true">'
                f'<b>{pin["number"]}</b></span>'
            )
            legend += (
                f'<li><span class="number">{pin["number"]}</span>'
                f'{escape(pin["label"])}</li>'
            )
        if legend:
            legend = f'<ol class="figure-key" aria-label="화면 번호 설명">{legend}</ol>'
        return (
            f'<figure class="screenshot {size}" id="screen-{name}" '
            f'style="--print-shot-width:{round(aspect * 515, 1)}pt">'
            f'<div class="screen-frame"><div class="screen-canvas">'
            f'<button type="button" class="screen-open" '
            f'aria-label="{escape(item["caption"], quote=True)} — 이미지 확대" '
            f'data-image-name="{name}">'
            f'<img src="data:image/webp;base64,{data}" '
            f'width="{item["width"]}" height="{item["height"]}" '
            f'alt="{escape(item["alt"], quote=True)}" loading="eager" decoding="async">'
            f'<span class="zoom-hint" aria-hidden="true">＋ 확대</span></button>'
            f'{pins}</div></div><figcaption>{escape(item["caption"])}</figcaption>{legend}</figure>'
        )

    html = (HERE / "guide.template.html").read_text()
    html = re.sub(r"\{\{shot:([a-z0-9-]+)\}\}", screenshot, html)
    brand = base64.b64encode((ROOT / "public/brand/transmeet.svg").read_bytes()).decode()
    html = html.replace("{{brand}}", f"data:image/svg+xml;base64,{brand}")
    html = html.replace("{{styles}}", (HERE / "guide.css").read_text())
    html = html.replace("{{script}}", (HERE / "guide.js").read_text())
    assert not re.search(r"\{\{[^}]+\}\}", html), "Unresolved guide placeholder"
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids)), "Duplicate document IDs"
    targets = re.findall(r'href="#([^"]+)"', html)
    assert set(targets) <= set(ids), f"Broken anchors: {set(targets) - set(ids)}"
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_text(html)
    print(f"Built {DESTINATION.relative_to(ROOT)}")
    print(f"{len(used)} embedded screenshots · {len(html.encode()):,} bytes · {len(targets)} valid anchors")


if __name__ == "__main__":
    build()
