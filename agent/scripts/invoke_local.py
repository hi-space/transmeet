#!/usr/bin/env python3
"""Run the meeting page agent locally against a deployed harness.

Usage:
  cd backend && python3 ../agent/scripts/invoke_local.py <meetingId>
  cd backend && python3 ../agent/scripts/invoke_local.py --sample ../agent/samples/short-1on1.json

Reads HARNESS_ARN / PAGES_BUCKET / PAGES_PUBLIC_BASE_URL from infra/outputs.json
when not set in the environment. Prints page_stream events as they arrive.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

OUTPUTS = ROOT / "infra" / "outputs.json"
if OUTPUTS.exists():
    out = json.loads(OUTPUTS.read_text())["TransmeetStack"]
    os.environ.setdefault("HARNESS_ARN", out["HarnessArn"])
    os.environ.setdefault("PAGES_BUCKET", out["PagesBucketName"])
    os.environ.setdefault("PAGES_PUBLIC_BASE_URL", out["CustomDomainUrl"].rstrip("/") + "/p")

from app.page_agent import run_page_generation  # noqa: E402


async def printer(event: dict) -> None:
    phase = event.get("phase")
    if phase == "delta":
        print(event.get("text", ""), end="", flush=True)
    elif phase == "status":
        print(f"\n[status] {event.get('text')}", flush=True)
    else:
        print(f"\n[{phase}] {json.dumps({k: v for k, v in event.items() if k not in ('type', 'phase')}, ensure_ascii=False)}")


async def main(argv):
    if len(argv) >= 3 and argv[1] == "--sample":
        sample = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        meeting_id = sample["meeting"]["meetingId"]
        inline = [
            {"speaker": m["speaker"], "original": m["original"], "translation": m.get("translation", ""),
             "timestamp": m["timestamp"]}
            for m in sample["messages"]
        ]
        # 샘플 미팅은 DynamoDB 에 없으므로 제목·시작 시각을 아이템에 먼저 넣어 둔다
        from app import aws_clients
        from app.config import config
        async with aws_clients.dynamodb_client() as ddb:
            await ddb.put_item(TableName=config.MEETINGS_TABLE, Item={
                "meetingId": {"S": meeting_id}, "title": {"S": sample["meeting"]["title"]},
                "createdAt": {"S": sample["meeting"]["startedAt"]}, "status": {"S": "sample"},
                "messages": {"L": []},
            })
        page = await run_page_generation(printer, meeting_id, inline_messages=inline)
    else:
        page = await run_page_generation(printer, argv[1])
    print("\nRESULT:", page.url if page else "failed")
    return 0 if page else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv)))
