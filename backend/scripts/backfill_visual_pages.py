#!/usr/bin/env python3
"""Backfill visual meeting pages, with an explicit account scope and local backups.

From the repository root:
  python3 backend/scripts/backfill_visual_pages.py --user-id USER_ID
  python3 backend/scripts/backfill_visual_pages.py --user-id USER_ID --apply

Defaults to the last 14 days by meeting creation time. Without --apply, only reads
AWS and writes a local report. Already visual pages and empty meetings are skipped.
Settings use backend configuration, with runtime/bucket fallbacks from CDK outputs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.page_agent import (  # noqa: E402
    PageAgentDeps, check_meeting_id, ddb_item_to_dict, load_page, merge_messages,
    page_key, run_page_generation,
)
from app.message_store import load_messages  # noqa: E402


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


async def recent_meetings(ddb, table: str, *, since: datetime, until: datetime,
                          user_id: str | None) -> list[dict]:
    """Read every scan page; compare parsed instants, not mixed ISO string formats."""
    selected = []
    token = None
    while True:
        response = await ddb.scan(
            TableName=table, ProjectionExpression="meetingId, title, createdAt, userId",
            **({"ExclusiveStartKey": token} if token else {}),
        )
        for raw in response.get("Items", []):
            item = ddb_item_to_dict(raw)
            if user_id is not None and item.get("userId") != user_id:
                continue
            try:
                created = parse_time(item.get("createdAt", ""))
            except (AttributeError, TypeError, ValueError):
                continue
            if since <= created <= until:
                check_meeting_id(item.get("meetingId"))
                selected.append(item)
        token = response.get("LastEvaluatedKey")
        if not token:
            return sorted(selected, key=lambda item: parse_time(item["createdAt"]))


class _StyleParser(HTMLParser):
    style = ""

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("name") == "transmeet:style":
            self.style = attributes.get("content", "")


def is_visual(html: bytes) -> bool:
    parser = _StyleParser()
    parser.feed(html.decode("utf-8"))
    return parser.style == "visual"


async def read_html(s3, bucket: str, meeting_id: str, page: dict | None) -> bytes:
    if not page:
        return b""
    try:
        response = await s3.get_object(Bucket=bucket, Key=page_key(meeting_id, page["pageId"]))
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return b""
        raise
    body = response["Body"]
    try:
        return await body.read()
    finally:
        body.close()


async def backfill_meeting(meeting_id: str, *, deps: PageAgentDeps, user_id: str | None,
                           apply: bool, output_dir: Path) -> dict:
    result = {"meetingId": meeting_id}
    errors = []

    async def progress(event):
        if event.get("phase") == "error":
            errors.append(event.get("error", "Generation failed"))
        if event.get("phase") != "delta" and (event.get("text") or event.get("error")):
            print(json.dumps(event, ensure_ascii=False), flush=True)

    try:
        check_meeting_id(meeting_id)
        async with deps.ddb_client() as ddb, deps.s3_client() as s3:
            response = await ddb.get_item(
                TableName=deps.table, Key={"meetingId": {"S": meeting_id}}, ConsistentRead=True,
            )
            item = ddb_item_to_dict(response.get("Item", {}))
            result["title"] = item.get("title", "")
            if not item or (user_id is not None and item.get("userId") != user_id):
                return {**result, "status": "outside_scope"}
            # 대화는 회의 항목이 아니라 메시지 테이블에 있다(이전 중 남은 옛 messages 속성은 보지 않는다).
            messages = await load_messages(ddb, meeting_id, table=deps.messages_table)
            if not merge_messages(messages):
                return {**result, "status": "empty"}
            before = await load_page(ddb, deps.pages_table, meeting_id)
            html = await read_html(s3, deps.bucket, meeting_id, before)
            if html and is_visual(html):
                return {**result, "status": "already_visual", "url": before["url"]}
            if not apply:
                return {**result, "status": "would_generate", "previousUrl": (before or {}).get("url")}

            destination = output_dir / meeting_id
            destination.mkdir(parents=True, exist_ok=True)
            # Keep the first backup even when retrying in the same output directory.
            if before and not (destination / "before.json").exists():
                (destination / "before.html").write_bytes(html)
                (destination / "before.json").write_text(
                    json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8",
                )

        page = await run_page_generation(
            progress, meeting_id, deps=deps, style="visual",
            reuse_existing=False, prefer_stored_messages=True,
        )
        if not page:
            raise RuntimeError("; ".join(errors) or "Generation did not produce a page")
        async with deps.ddb_client() as ddb, deps.s3_client() as s3:
            current = await load_page(ddb, deps.pages_table, meeting_id)
            if not current or current["pageId"] != page["pageId"]:
                raise RuntimeError("Published page does not match the current page record")
            html = await read_html(s3, deps.bucket, meeting_id, current)
        if not html or not is_visual(html):
            raise RuntimeError("Published HTML is missing or does not have the visual style")
        (destination / "after.html").write_bytes(html)
        return {
            **result, "status": "generated", "url": current["url"],
            "pageId": current["pageId"], "generatedAt": current.get("generatedAt"),
            "previousUrl": (before or {}).get("url"),
        }
    except Exception as exc:
        return {**result, "status": "failed", "error": str(exc)}


async def run(args, deps: PageAgentDeps) -> int:
    async with deps.ddb_client() as ddb:
        meetings = await recent_meetings(
            ddb, deps.table, since=args.since, until=args.until, user_id=args.user_id,
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / ("report.json" if args.apply else "dry-run.json")
    report = {
        "since": args.since.isoformat(), "until": args.until.isoformat(),
        "userId": args.user_id, "apply": args.apply, "style": "visual",
        "results": [{**item, "status": "pending"} for item in meetings],
    }

    def checkpoint():
        temporary = report_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(report_path)

    checkpoint()
    semaphore = asyncio.Semaphore(args.concurrency)

    async def process(index, item):
        async with semaphore:
            result = await backfill_meeting(
                item["meetingId"], deps=deps, user_id=args.user_id,
                apply=args.apply, output_dir=args.output_dir,
            )
            report["results"][index] = {**item, **result}
            checkpoint()
            print(json.dumps(result, ensure_ascii=False), flush=True)

    await asyncio.gather(*(process(index, item) for index, item in enumerate(meetings)))
    counts = Counter(row["status"] for row in report["results"])
    print(json.dumps({"counts": counts, "report": str(report_path)}, ensure_ascii=False), flush=True)
    return 1 if counts["failed"] else 0


def main() -> int:
    now = datetime.now(timezone.utc)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--user-id", help="Only this meeting owner's records")
    scope.add_argument("--all-users", action="store_true", help="Include meetings from every account")
    parser.add_argument("--since", type=parse_time, default=now - timedelta(days=14))
    parser.add_argument("--until", type=parse_time, default=now)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--concurrency", type=int, default=4, help="Simultaneous generations (default: 4)")
    parser.add_argument("--output-dir", type=Path, default=(
        ROOT / "agent" / ".page-backup" / f"visual-{now:%Y%m%d-%H%M%S}"
    ))
    args = parser.parse_args()
    if args.since > args.until or not 1 <= args.concurrency <= 4:
        parser.error("The time window must be ordered and concurrency must be between 1 and 4")

    outputs_path = ROOT / "infra" / "outputs.json"
    outputs = json.loads(outputs_path.read_text()).get("TransmeetStack", {}) if outputs_path.exists() else {}
    deps = PageAgentDeps.from_config()
    deps.runtime_arn = deps.runtime_arn or outputs.get("MeetingPageRuntimeArn", "")
    deps.bucket = deps.bucket or outputs.get("PagesBucketName", "")
    if not deps.runtime_arn or not deps.bucket:
        parser.error("Configure PAGE_AGENT_RUNTIME_ARN and PAGES_BUCKET, or provide infra/outputs.json")
    return asyncio.run(run(args, deps))


if __name__ == "__main__":
    sys.exit(main())
