#!/usr/bin/env python3
"""One-off: move `page` records from meeting items into the pages table.

Usage (from agent/, using the venv):
  .venv/bin/python scripts/migrate_pages.py            # dry run — prints what would move
  .venv/bin/python scripts/migrate_pages.py --apply    # copy into the pages table, then REMOVE page from meetings

Idempotent: meetings without `page` are skipped; an existing pages-table record is kept when it is
newer (by generatedAt) than the meeting's `page`. The meeting item is only touched (REMOVE page)
after the pages-table write succeeded.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import boto3
from boto3.dynamodb.types import TypeSerializer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime.publish import ddb_item_to_dict  # noqa: E402

REGION = os.environ.get("REGION", "us-east-1")
MEETINGS = os.environ.get("MEETINGS_TABLE", "transmeet-meetings")
PAGES = os.environ.get("PAGES_TABLE", "transmeet-pages")
_ser = TypeSerializer()


def scan(ddb, table: str, **kw):
    token = None
    while True:
        res = ddb.scan(TableName=table, **kw, **({"ExclusiveStartKey": token} if token else {}))
        yield from res.get("Items", [])
        token = res.get("LastEvaluatedKey")
        if not token:
            return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    ddb = boto3.client("dynamodb", region_name=REGION)

    existing = {i["meetingId"]["S"]: ddb_item_to_dict(i) for i in scan(ddb, PAGES)}
    moved = kept = 0
    for raw in scan(ddb, MEETINGS, ProjectionExpression="meetingId, page"):
        item = ddb_item_to_dict(raw)
        page = item.get("page")
        if not isinstance(page, dict) or not page.get("pageId"):
            continue
        mid = item["meetingId"]
        current = existing.get(mid)
        if current and str(current.get("generatedAt", "")) >= str(page.get("generatedAt", "")):
            action = "keep newer pages-table record, drop meeting.page"
            kept += 1
        else:
            action = f"move pageId={page['pageId']}"
            moved += 1
        print(f"{mid} {page.get('title', '')[:30]!r}: {action}")
        if not args.apply:
            continue
        if not (current and str(current.get("generatedAt", "")) >= str(page.get("generatedAt", ""))):
            record = {"meetingId": mid, **{k: page[k] for k in ("pageId", "url", "title", "shareSummary", "generatedAt") if k in page},
                      "updatedAt": page.get("generatedAt", "")}
            ddb.put_item(TableName=PAGES, Item={k: _ser.serialize(v) for k, v in record.items()})
        ddb.update_item(TableName=MEETINGS, Key={"meetingId": {"S": mid}}, UpdateExpression="REMOVE page")
    print(f"{'applied' if args.apply else 'dry run'}: moved={moved} kept={kept}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
