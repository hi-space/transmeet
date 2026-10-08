#!/usr/bin/env python3
"""Render a topic timeline as inline SVG.

Usage: python3 timeline_svg.py input.json > timeline.svg
input: {"start":"HH:MM","end":"HH:MM","topics":[{"label":"...","start":"HH:MM","end":"HH:MM"}]}
Colors come only from CSS variables (--tm-*) so the page's light/dark theme applies.
"""
import json
import sys
from html import escape

W, LEFT, RIGHT, ROW, TOP, BOTTOM = 800, 170, 24, 30, 28, 24


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def render_timeline(data: dict) -> str:
    topics = data["topics"]
    t0, t1 = _mins(data["start"]), _mins(data["end"])
    span = max(t1 - t0, 1)
    plot_w = W - LEFT - RIGHT
    height = TOP + ROW * len(topics) + BOTTOM
    x_of = lambda t: LEFT + (t - t0) / span * plot_w
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" role="img" font-family="var(--tm-font, sans-serif)" font-size="13">',
        f"<title>{escape('주제 타임라인')}</title>",
        f"<desc>{escape(data['start'])}부터 {escape(data['end'])}까지 {len(topics)}개 주제의 논의 구간</desc>",
        f'<line x1="{LEFT}" y1="{TOP - 8}" x2="{W - RIGHT}" y2="{TOP - 8}" stroke="var(--tm-line)" />',
    ]
    ticks = 4
    for i in range(ticks + 1):
        t = t0 + span * i / ticks
        x = x_of(t)
        label = f"{int(t) // 60:02d}:{int(t) % 60:02d}"
        out.append(f'<text x="{x:.1f}" y="{TOP - 14}" text-anchor="middle" fill="var(--tm-muted)" font-size="11">{label}</text>')
    for i, tp in enumerate(topics):
        y = TOP + i * ROW
        xs, xe = x_of(_mins(tp["start"])), x_of(_mins(tp["end"]))
        w = max(xe - xs, 3)
        out.append(f'<text x="{LEFT - 10}" y="{y + 18}" text-anchor="end" fill="currentColor">{tp["label"]}</text>')
        out.append(f'<rect class="bar" x="{xs:.1f}" y="{y + 6}" width="{w:.1f}" height="16" rx="4" fill="var(--tm-accent)" opacity="0.85"><title>{escape(tp["label"])} {escape(tp["start"])}–{escape(tp["end"])}</title></rect>')
    out.append("</svg>")
    return "\n".join(out)


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        print(render_timeline(json.load(f)))
