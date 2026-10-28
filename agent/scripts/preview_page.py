#!/usr/bin/env python3
"""Generate a meeting page with the real model but save it locally instead of publishing.

Usage (from agent/, using the venv):
  .venv/bin/python scripts/preview_page.py <meetingId> --style visual [--out /tmp/preview]

Reads the meeting from DynamoDB (read-only), runs the full graph in this process with real
Bedrock and Web Search, and writes `<out>/<meetingId>-<style>.html`. Nothing is written to S3 or
DynamoDB, so it is safe to run on a meeting whose published page must not change.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(AGENT / "scripts"))

from invoke_local import apply_defaults, load_outputs, payload_from_meeting, print_event  # noqa: E402
from runtime.app import build_deps, run_page_graph  # noqa: E402
from runtime.graph import STYLES  # noqa: E402
from runtime.publish import PublishedPage  # noqa: E402
from runtime.settings import Settings  # noqa: E402
from runtime.web_search import WebSearchClient  # noqa: E402


async def main_async(meeting_id: str, style: str, out: Path) -> int:
    apply_defaults(load_outputs())
    payload = payload_from_meeting(meeting_id)
    payload["meeting"]["style"] = style
    settings = Settings.from_env()
    web_search = WebSearchClient(settings.gateway_url, settings.region) if settings.gateway_url else None
    deps = build_deps(settings, web_search)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{meeting_id}-{style}.html"

    async def save(mid: str, html: str, title: str, summary: str) -> PublishedPage:
        target.write_text(html, encoding="utf-8")
        return PublishedPage("preview", target.as_uri(), "", title, summary)

    deps.publisher = save
    started, ok = time.time(), False
    try:
        async for evt in run_page_graph(payload, session_id=uuid.uuid4().hex + uuid.uuid4().hex[:8], deps=deps,
                                        budget_s=settings.budget_s):
            print_event(evt)
            ok = ok or evt.get("phase") == "done"
    finally:
        if web_search is not None:
            await web_search.aclose()
    print(f"\n{'saved' if ok else 'FAILED'} {target} ({time.time() - started:.0f}s)")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("meeting_id")
    ap.add_argument("--style", choices=sorted(STYLES), default="visual")
    ap.add_argument("--out", type=Path, default=Path("/tmp/transmeet-preview"))
    args = ap.parse_args()
    return asyncio.run(main_async(args.meeting_id, args.style, args.out))


if __name__ == "__main__":
    sys.exit(main())
