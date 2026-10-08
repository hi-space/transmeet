#!/usr/bin/env python3
"""Render a small decision/flow diagram as inline SVG (layered left-to-right).

Usage: python3 flow_svg.py input.json > flow.svg
input: {"title":"...","nodes":[{"id","label","kind":"step|decision|outcome"}],"edges":[{"from","to","label"?}]}
Keep it under ~12 nodes; the layout is a simple longest-path layering.
"""
import json
import sys
from html import escape

NODE_W, NODE_H, GAP_X, GAP_Y, PAD = 170, 56, 90, 28, 24


def _layers(nodes, edges):
    ids = [n["id"] for n in nodes]
    known = set(ids)
    for e in edges:
        for k in ("from", "to"):
            if e[k] not in known:
                raise ValueError(f"edge references unknown node: {e[k]}")
    depth = {i: 0 for i in ids}
    for _ in range(len(ids)):
        for e in edges:
            depth[e["to"]] = max(depth[e["to"]], depth[e["from"]] + 1)
    cols = {}
    for i in ids:
        cols.setdefault(depth[i], []).append(i)
    return cols


def render_flow(data: dict) -> str:
    nodes = {n["id"]: n for n in data["nodes"]}
    cols = _layers(data["nodes"], data["edges"])
    ncols, nrows = len(cols), max(len(v) for v in cols.values())
    width = PAD * 2 + ncols * NODE_W + (ncols - 1) * GAP_X
    height = PAD * 2 + nrows * NODE_H + (nrows - 1) * GAP_Y
    pos = {}
    for c, ids in sorted(cols.items()):
        col_h = len(ids) * NODE_H + (len(ids) - 1) * GAP_Y
        y0 = (height - col_h) / 2
        for r, i in enumerate(ids):
            pos[i] = (PAD + c * (NODE_W + GAP_X), y0 + r * (NODE_H + GAP_Y))
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" font-family="var(--tm-font, sans-serif)" font-size="13">',
        f"<title>{escape(data.get('title', '흐름도'))}</title>",
        f"<desc>{len(nodes)}개 단계와 {len(data['edges'])}개 연결로 이루어진 흐름</desc>",
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="var(--tm-muted)"/></marker></defs>',
    ]
    for e in data["edges"]:
        x1, y1 = pos[e["from"]]
        x2, y2 = pos[e["to"]]
        sx, sy, ex, ey = x1 + NODE_W, y1 + NODE_H / 2, x2, y2 + NODE_H / 2
        mx = (sx + ex) / 2
        out.append(f'<path d="M{sx:.1f} {sy:.1f} C{mx:.1f} {sy:.1f} {mx:.1f} {ey:.1f} {ex:.1f} {ey:.1f}" fill="none" stroke="var(--tm-muted)" stroke-width="1.5" marker-end="url(#arrow)"/>')
        if e.get("label"):
            out.append(f'<text x="{mx:.1f}" y="{(sy + ey) / 2 - 6:.1f}" text-anchor="middle" fill="var(--tm-muted)" font-size="11">{escape(e["label"])}</text>')
    for i, (x, y) in pos.items():
        n = nodes[i]
        kind = n.get("kind", "step")
        cx, cy = x + NODE_W / 2, y + NODE_H / 2
        if kind == "decision":
            pts = f"{cx},{y} {x + NODE_W},{cy} {cx},{y + NODE_H} {x},{cy}"
            out.append(f'<polygon points="{pts}" fill="var(--tm-warn-soft)" stroke="var(--tm-warn)" stroke-width="1.5"/>')
        elif kind == "outcome":
            out.append(f'<rect x="{x}" y="{y}" width="{NODE_W}" height="{NODE_H}" rx="28" fill="var(--tm-ok-soft)" stroke="var(--tm-ok)" stroke-width="1.5"/>')
        else:
            out.append(f'<rect x="{x}" y="{y}" width="{NODE_W}" height="{NODE_H}" rx="10" fill="var(--tm-card)" stroke="var(--tm-accent)" stroke-width="1.5"/>')
        out.append(f'<text x="{cx}" y="{cy + 4.5}" text-anchor="middle" fill="currentColor" font-weight="600">{n["label"]}</text>')
    out.append("</svg>")
    return "\n".join(out)


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        print(render_flow(json.load(f)))
