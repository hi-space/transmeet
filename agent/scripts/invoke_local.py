#!/usr/bin/env python3
"""Run the meeting page agent — in this process (`--direct`) or on the deployed Runtime (`--runtime`).

Usage (from agent/, using the venv):
  .venv/bin/python scripts/invoke_local.py --direct  --sample samples/short-1on1.json
  .venv/bin/python scripts/invoke_local.py --direct  <meetingId>
  .venv/bin/python scripts/invoke_local.py --runtime --sample samples/tech-review.json
  .venv/bin/python scripts/invoke_local.py --runtime <meetingId> [--runtime-arn ARN]

Settings come from the environment, falling back to infra/outputs.json (PagesBucketName,
CustomDomainUrl, MeetingPageRuntimeArn) and to the `transmeet-web-search` Gateway URL.
Both modes print status/delta events as they arrive and exit 0 only on `done`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import boto3

AGENT = Path(__file__).resolve().parents[1]
ROOT = AGENT.parent
sys.path.insert(0, str(AGENT))

OUTPUTS = ROOT / "infra" / "outputs.json"
REGION = os.environ.get("REGION", "us-east-1")


def load_outputs() -> dict:
    if OUTPUTS.exists():
        return json.loads(OUTPUTS.read_text())["TransmeetStack"]
    return {}


def apply_defaults(out: dict) -> None:
    if out.get("PagesBucketName"):
        os.environ.setdefault("PAGES_BUCKET", out["PagesBucketName"])
    if out.get("CustomDomainUrl"):
        os.environ.setdefault("PAGES_PUBLIC_BASE_URL", out["CustomDomainUrl"].rstrip("/") + "/p")
    if not os.environ.get("WEB_SEARCH_GATEWAY_URL"):
        cp = boto3.client("bedrock-agentcore-control", region_name=REGION)
        token, url = None, ""
        while True:
            kw = {"maxResults": 50, **({"nextToken": token} if token else {})}
            res = cp.list_gateways(**kw)
            for g in res.get("items", []):
                if g.get("name") == "transmeet-web-search":
                    url = cp.get_gateway(gatewayIdentifier=g["gatewayId"]).get("gatewayUrl", "")
            token = res.get("nextToken")
            if url or not token:
                break
        if url:
            os.environ["WEB_SEARCH_GATEWAY_URL"] = url


MERGE_WINDOW_S = 10  # 백엔드 build_meeting_payload 와 같은 규칙: 10초 이내 같은 화자 연속 발언은 한 턴
SPEAKER_LABELS = {"speaker1": "Speaker 1", "speaker2": "Speaker 2", "me": "Me"}


def _ts(v):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def build_payload(meeting: dict, raw_messages: list, model_id: str) -> dict:
    msgs = []
    for m in raw_messages:
        original = (m.get("originalText") or m.get("original") or "").strip()
        if not original:
            continue
        msgs.append({"timestamp": m.get("timestamp", ""), "speaker": m.get("speaker") or "speaker1", "original": original,
                     "translation": (m.get("translatedText") or m.get("translation") or "").strip(),
                     "detectedLanguage": m.get("detectedLanguage") or ""})
    msgs.sort(key=lambda m: (_ts(m["timestamp"]) is None, _ts(m["timestamp"]) or 0))
    merged = []
    for m in msgs:
        if merged and merged[-1]["speaker"] == m["speaker"]:
            a, b = _ts(merged[-1]["timestamp"]), _ts(m["timestamp"])
            if a and b and 0 <= (b - a).total_seconds() < MERGE_WINDOW_S:
                merged[-1]["original"] += " " + m["original"]
                merged[-1]["translation"] = " ".join(x for x in (merged[-1]["translation"], m["translation"]) if x)
                merged[-1]["timestamp"] = m["timestamp"]
                continue
        merged.append(dict(m))
    speakers, langs = [], []
    for m in merged:
        lab = SPEAKER_LABELS.get(m["speaker"], m["speaker"])
        if lab not in speakers:
            speakers.append(lab)
        if m["detectedLanguage"] and m["detectedLanguage"] not in langs:
            langs.append(m["detectedLanguage"])
    from datetime import datetime, timezone
    return {"meeting": {"meetingId": meeting["meetingId"], "title": meeting.get("title", ""),
                        "startedAt": meeting.get("createdAt") or meeting.get("startedAt") or (merged[0]["timestamp"] if merged else ""),
                        "endedAt": merged[-1]["timestamp"] if merged else "", "speakers": speakers, "languages": langs,
                        "generatedAt": datetime.now(timezone.utc).isoformat(), "modelId": model_id},
            "messages": merged}


def payload_from_sample(path: Path) -> dict:
    sample = json.loads(path.read_text(encoding="utf-8"))
    item = {"meetingId": sample["meeting"]["meetingId"], "title": sample["meeting"]["title"],
            "createdAt": sample["meeting"]["startedAt"]}
    payload = build_payload(item, sample["messages"], sample["meeting"].get("modelId") or "us.anthropic.claude-opus-5-5")
    # 샘플 미팅은 DynamoDB 에 없으므로 발행(조건부 UpdateItem)이 통과하도록 아이템을 넣어 둔다
    ddb = boto3.client("dynamodb", region_name=REGION)
    ddb.put_item(TableName=os.environ.get("MEETINGS_TABLE", "transmeet-meetings"), Item={
        "meetingId": {"S": item["meetingId"]}, "title": {"S": item["title"]},
        "createdAt": {"S": item["createdAt"]}, "status": {"S": "sample"}, "messages": {"L": []},
    })
    return payload


def payload_from_meeting(meeting_id: str) -> dict:
    from runtime.publish import ddb_item_to_dict  # noqa: E402
    ddb = boto3.client("dynamodb", region_name=REGION)
    res = ddb.get_item(TableName=os.environ.get("MEETINGS_TABLE", "transmeet-meetings"), Key={"meetingId": {"S": meeting_id}})
    if "Item" not in res:
        raise SystemExit(f"meeting not found: {meeting_id}")
    item = ddb_item_to_dict(res["Item"])
    return build_payload(item, item.get("messages") or [], "us.anthropic.claude-opus-5-5")


def print_event(event: dict) -> None:
    phase = event.get("phase")
    if phase == "delta":
        print(event.get("text", ""), end="", flush=True)
    elif phase == "status":
        print(f"\n[status {time.strftime('%H:%M:%S')}] {event.get('text')}", flush=True)
    else:
        print(f"\n[{phase}] {json.dumps({k: v for k, v in event.items() if k != 'phase'}, ensure_ascii=False)}", flush=True)


async def run_direct(payload: dict) -> bool:
    from runtime.app import generate  # noqa: E402
    ok = False
    async for evt in generate(payload, session_id=uuid.uuid4().hex + uuid.uuid4().hex[:8]):
        print_event(evt)
        if evt.get("phase") == "done":
            ok = True
    return ok


def run_runtime(payload: dict, runtime_arn: str) -> bool:
    from botocore.config import Config
    client = boto3.client("bedrock-agentcore", region_name=REGION, config=Config(read_timeout=2700, retries={"max_attempts": 0}))
    res = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn, runtimeSessionId=uuid.uuid4().hex + uuid.uuid4().hex[:8],
        contentType="application/json", accept="text/event-stream", payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    ok = False
    # botocore 의 read(amt) 는 amt 바이트가 모일 때까지 막힌다 — 기본 1024 면 이벤트가 10개씩 몰려 온다.
    for raw in res["response"].iter_lines(chunk_size=64):
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if not line.startswith("data: "):
            continue
        try:
            evt = json.loads(line[6:])
        except json.JSONDecodeError:
            print("\n[raw]", line)
            continue
        print_event(evt)
        if evt.get("phase") == "done":
            ok = True
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--direct", action="store_true")
    mode.add_argument("--runtime", action="store_true")
    ap.add_argument("--sample", type=Path)
    ap.add_argument("meeting_id", nargs="?")
    ap.add_argument("--runtime-arn", default=os.environ.get("PAGE_AGENT_RUNTIME_ARN"))
    args = ap.parse_args()

    out = load_outputs()
    apply_defaults(out)
    payload = payload_from_sample(args.sample) if args.sample else payload_from_meeting(args.meeting_id or "")
    print(f"meeting={payload['meeting']['meetingId']} messages={len(payload['messages'])} model={payload['meeting']['modelId']}")
    started = time.time()
    if args.direct:
        ok = asyncio.run(run_direct(payload))
    else:
        arn = args.runtime_arn or out.get("MeetingPageRuntimeArn")
        if not arn:
            raise SystemExit("runtime ARN unknown: pass --runtime-arn or deploy infra first")
        ok = run_runtime(payload, arn)
    print(f"\nRESULT: {'done' if ok else 'failed'} in {time.time() - started:.0f}s")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
