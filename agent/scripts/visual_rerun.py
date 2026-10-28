#!/usr/bin/env python3
"""Re-run only the `visual` page stages (runtime/visual.py) against a finished graph workspace.

Usage (from agent/, using the venv):
  .venv/bin/python scripts/visual_rerun.py <work_dir> --out /tmp/visual
  .venv/bin/python scripts/visual_rerun.py <work_dir> --out /tmp/visual --render-only

`<work_dir>` is a graph workspace (/tmp/transmeet-work/<session>) — its analysis.json, transcript,
research.html and insights.html are reused, so analyze/research/insights do not run again.
Writes visual/{plan,write,final,svgs}.json and page.html under `<out>/`. `--render-only` re-renders
`<out>/visual/final.json` + `svgs.json` with the current CSS and layout code (no model calls).
Nothing is published.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))

from runtime import visual  # noqa: E402
from runtime.graph import _terms_block  # noqa: E402
from runtime.meta import derive_meta  # noqa: E402
from runtime.model import PRIMARY_MODEL_ID, make_chat_model  # noqa: E402
from runtime.validator import validate_html  # noqa: E402

CSS = AGENT / "skills/meeting-minutes-page/assets/visual.css"


def load_inputs(ws: Path) -> visual.VisualInputs:
    meeting = json.loads((ws / "meeting.json").read_text())
    messages = json.loads((ws / "transcript.json").read_text())
    analysis = json.loads((ws / "analysis.json").read_text())
    read = lambda name: (ws / name).read_text() if (ws / name).exists() else ""  # noqa: E731
    return visual.VisualInputs(meta=derive_meta(meeting, messages), analysis=analysis, transcript=read("transcript.txt"),
                               terms_block=_terms_block(analysis), research_html=read("research.html"), insights_html=read("insights.html"))


async def run(ws: Path, out: Path, model_id: str, render_only: bool) -> int:
    inp, css = load_inputs(ws), CSS.read_text(encoding="utf-8").strip()
    out.mkdir(parents=True, exist_ok=True)

    def save(name: str, obj) -> None:
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")

    started = time.time()
    if render_only:
        written = json.loads((out / "visual/final.json").read_text())
        svgs = json.loads((out / "visual/svgs.json").read_text())
        html = visual.render_page(inp, written, svgs, css)
    else:
        llm = make_chat_model(model_id, region="us-east-1")
        result = await visual.build_visual_page(lambda stage: llm, inp, css, save=save)
        html, svgs = result.html, result.svgs
        print(f"usage={result.usage} fixes={result.fixes}", flush=True)
    errors = validate_html(html)
    (out / "page.html").write_text(html, encoding="utf-8")
    missing = [k for k, v in svgs.items() if not v]
    print(f"{time.time() - started:.0f}s, {len(html):,} bytes, errors={errors[:5]}, missing figures={missing}", flush=True)
    return 0 if not errors else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("work_dir", type=Path)
    ap.add_argument("--out", type=Path, default=Path("/tmp/transmeet-visual"))
    ap.add_argument("--model", default=PRIMARY_MODEL_ID)
    ap.add_argument("--render-only", action="store_true")
    args = ap.parse_args()
    return asyncio.run(run(args.work_dir, args.out, args.model, args.render_only))


if __name__ == "__main__":
    sys.exit(main())
