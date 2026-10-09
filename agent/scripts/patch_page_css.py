#!/usr/bin/env python3
"""One-off: apply the `.time` nowrap fix to already-published pages in S3.

Published pages inline the template CSS, so a template fix only reaches pages generated afterwards.
This rewrites the single CSS rule in every existing `p/<meetingId>/<pageId>.html` object without
touching the model-generated body.

Usage (from agent/, using the venv):
  .venv/bin/python scripts/patch_page_css.py                 # dry run — lists objects that would change
  .venv/bin/python scripts/patch_page_css.py --apply         # back up originals to ./.page-backup/ then upload

Idempotent: objects that no longer contain the old rule are skipped.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import boto3

REGION = os.environ.get("REGION", "us-east-1")
BACKUP_DIR = Path(__file__).resolve().parents[1] / ".page-backup"

# Keep in sync with skills/meeting-minutes-page/assets/template.html.
REPLACEMENTS = [
    (
        ".time{font-family:var(--tm-mono);font-size:.78rem;color:var(--tm-muted);white-space:nowrap}",
        ".time{font-family:var(--tm-mono);font-size:.78rem;color:var(--tm-muted)}span.time{white-space:nowrap}",
    ),
    (
        ".glossary td.time{white-space:normal;line-height:1.5}",
        ".glossary td.time{line-height:1.5}",
    ),
]


def patch_html(html: str) -> tuple[str, bool]:
    """Return (patched_html, changed). Only the first replacement decides `changed`."""
    primary_old = REPLACEMENTS[0][0]
    if primary_old not in html:
        return html, False
    for old, new in REPLACEMENTS:
        html = html.replace(old, new)
    return html, True


def list_pages(s3, bucket: str):
    token = None
    while True:
        kw = {"ContinuationToken": token} if token else {}
        res = s3.list_objects_v2(Bucket=bucket, Prefix="p/", **kw)
        for obj in res.get("Contents", []):
            if obj["Key"].endswith(".html"):
                yield obj["Key"]
        token = res.get("NextContinuationToken")
        if not token:
            return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--bucket", default=os.environ.get("PAGES_BUCKET"))
    args = ap.parse_args()
    bucket = args.bucket
    if not bucket:
        account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
        bucket = f"transmeet-meeting-pages-{account}"
    s3 = boto3.client("s3", region_name=REGION)

    changed = skipped = 0
    for key in list_pages(s3, bucket):
        obj = s3.get_object(Bucket=bucket, Key=key)
        html = obj["Body"].read().decode("utf-8")
        patched, did = patch_html(html)
        if not did:
            skipped += 1
            continue
        changed += 1
        print(("patch " if args.apply else "would patch ") + key)
        if not args.apply:
            continue
        backup = BACKUP_DIR / key
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(html, encoding="utf-8")
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=patched.encode("utf-8"),
            ContentType=obj.get("ContentType", "text/html; charset=utf-8"),
            CacheControl=obj.get("CacheControl", "no-cache"),
        )
    print(f"\n{bucket}: {'patched' if args.apply else 'would patch'} {changed}, already fine {skipped}")
    if args.apply and changed:
        print(f"originals backed up under {BACKUP_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
