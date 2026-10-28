#!/usr/bin/env python3
"""Generate pages for many meetings on the deployed Runtime, N at a time.

Usage (from agent/, using the venv):
  .venv/bin/python scripts/invoke_batch.py [--workers 12] [--min-messages 10] [--include-existing]
                                           [--only id,id,...] [--out /tmp/batch.jsonl]

Reads every meeting from the meetings table (utterances from the messages table), skips `sample-*` items, meetings with fewer than
--min-messages raw utterances and (unless --include-existing) meetings that already have a page.
Each meeting runs as its own Runtime session; a transient failure is retried once with a new
session. Results are appended to --out as JSON lines and summarised at the end.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore.config import Config

sys.path.insert(0, str(Path(__file__).resolve().parent))
import invoke_local as il  # noqa: E402

TABLE = il.MEETINGS_TABLE
PAGES_TABLE = os.environ.get("PAGES_TABLE", "transmeet-pages")
_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def list_meetings() -> list:
    """회의 항목을 모두 읽고, 각 회의의 대화를 메시지 테이블에서 읽어 item["messages"] 에 붙인다."""
    from runtime.publish import ddb_item_to_dict  # noqa: E402
    ddb = boto3.client("dynamodb", region_name=il.REGION)
    items, token = [], None
    while True:
        kw = {"TableName": TABLE, **({"ExclusiveStartKey": token} if token else {})}
        res = ddb.scan(**kw)
        items.extend(ddb_item_to_dict(i) for i in res.get("Items", []))
        token = res.get("LastEvaluatedKey")
        if not token:
            break
    for item in items:
        item["messages"] = il.load_messages(item["meetingId"], ddb)
    return items


def meetings_with_pages() -> set:
    ddb = boto3.client("dynamodb", region_name=il.REGION)
    ids, token = set(), None
    while True:
        kw = {"TableName": PAGES_TABLE, "ProjectionExpression": "meetingId", **({"ExclusiveStartKey": token} if token else {})}
        res = ddb.scan(**kw)
        ids.update(i["meetingId"]["S"] for i in res.get("Items", []))
        token = res.get("LastEvaluatedKey")
        if not token:
            break
    return ids


def run_one(item: dict, runtime_arn: str, client) -> dict:
    meeting_id = item["meetingId"]
    payload = il.build_payload(item, item.get("messages") or [], "global.anthropic.claude-opus-5-5")
    result = {"meetingId": meeting_id, "title": item.get("title", ""), "messages": len(payload["messages"]),
              "raw_messages": len(item.get("messages") or []), "ok": False, "attempts": 0}
    started = time.time()
    for attempt in (1, 2):
        result["attempts"] = attempt
        last_status, error, transient = "", None, False
        try:
            res = client.invoke_agent_runtime(
                agentRuntimeArn=runtime_arn, runtimeSessionId=uuid.uuid4().hex + uuid.uuid4().hex[:8],
                contentType="application/json", accept="text/event-stream",
                payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
            for raw in res["response"].iter_lines(chunk_size=64):
                line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                if not line.startswith("data: "):
                    continue
                try:
                    evt = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                phase = evt.get("phase")
                if phase == "status":
                    last_status = evt.get("text", "")
                elif phase == "done":
                    result.update(ok=True, url=(evt.get("page") or {}).get("url"), usage=evt.get("usage"))
                elif phase == "error":
                    error, transient = evt.get("error"), bool(evt.get("transient"))
        except Exception as e:  # 네트워크·SDK 오류 — 새 세션으로 1회 재시도
            error, transient = f"{type(e).__name__}: {e}", True
        if result["ok"]:
            break
        result["error"], result["last_status"] = error or "stream ended without done", last_status
        if not transient:
            break
        log(f"retry {meeting_id} ({result['title'][:30]}): {error}")
    result["seconds"] = round(time.time() - started)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--min-messages", type=int, default=10)
    ap.add_argument("--include-existing", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated meetingIds")
    ap.add_argument("--out", type=Path, default=Path("/tmp/invoke_batch.jsonl"))
    ap.add_argument("--runtime-arn", default=os.environ.get("PAGE_AGENT_RUNTIME_ARN"))
    args = ap.parse_args()

    out = il.load_outputs()
    arn = args.runtime_arn or out.get("MeetingPageRuntimeArn")
    if not arn:
        raise SystemExit("runtime ARN unknown: pass --runtime-arn or deploy infra first")

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    have_page = set() if (args.include_existing or only) else meetings_with_pages()
    todo, skipped = [], []
    for it in list_meetings():
        mid = it["meetingId"]
        n = len(it.get("messages") or [])
        if only and mid not in only:
            continue
        if not only and mid.startswith("sample-"):
            skipped.append((mid, "sample"))
        elif n < args.min_messages:
            skipped.append((mid, f"{n} messages"))
        elif mid in have_page:
            skipped.append((mid, "page exists"))
        else:
            todo.append(it)
    todo.sort(key=lambda it: -len(it.get("messages") or []))  # 긴 미팅부터 시작해 꼬리 시간을 줄인다
    log(f"{len(todo)} meetings to run, {len(skipped)} skipped, workers={args.workers}, out={args.out}")
    for mid, why in skipped:
        log(f"  skip {mid}: {why}")

    client = boto3.client("bedrock-agentcore", region_name=il.REGION,
                          config=Config(read_timeout=2700, retries={"max_attempts": 0}, max_pool_connections=max(16, args.workers * 2)))
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool, args.out.open("a", encoding="utf-8") as fh:
        futures = {pool.submit(run_one, it, arn, client): it for it in todo}
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            state = "done" if r["ok"] else "FAIL"
            extra = r.get("url", "") if r["ok"] else f"{r.get('error')} (after: {r.get('last_status')})"
            log(f"{state} {len(results)}/{len(todo)} {r['seconds']}s {r['messages']}msg {r['title'][:28]!r} {extra}")

    ok = [r for r in results if r["ok"]]
    fail = [r for r in results if not r["ok"]]
    usage = {}
    for r in ok:
        for k, v in (r.get("usage") or {}).items():
            usage[k] = usage.get(k, 0) + int(v)
    log(f"SUMMARY ok={len(ok)} fail={len(fail)} usage={usage}")
    for r in fail:
        log(f"  failed {r['meetingId']} {r['title'][:30]!r}: {r.get('error')}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
