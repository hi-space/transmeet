"""Backfill only the requested meetings, preserving a backup before replacement."""
import copy
import io
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from boto3.dynamodb.types import TypeSerializer

from app.page_agent import PageAgentDeps
from scripts import backfill_visual_pages as backfill


SINCE = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
UNTIL = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
VISUAL = b'<html><head><meta content="visual" name="transmeet:style"></head><body>New page</body></html>'
LEGACY = b"<html><head><title>Old page</title></head><body>Old page</body></html>"


def encode(item):
    return {key: TypeSerializer().serialize(value) for key, value in item.items()}


def meeting(mid="m1", **extra):
    return {
        "meetingId": mid, "userId": "u1", "createdAt": "2026-10-20T00:00:00Z",
        "title": "Weekly sync",
        **extra,
    }


def stored(text, mid="a"):
    """메시지 테이블 항목(회의 항목이 아니라 별도 테이블에 한 건씩 있다)."""
    return {"meetingId": "m1", "sk": f"2026-10-20T00:01:00Z#{mid}", "id": mid, "speaker": "them",
            "originalText": text, "translatedText": "", "detectedLanguage": "en",
            "timestamp": "2026-10-20T00:01:00Z"}


@pytest.fixture
def env():
    records = {"m1": meeting()}
    messages = {"m1": [stored("Ship on Friday.")]}
    pages = {}
    objects = {}
    payloads = []
    scan_pages = []
    scan_calls = []

    class DDB:
        async def scan(self, **kwargs):
            scan_calls.append(kwargs)
            index = int(kwargs.get("ExclusiveStartKey", {}).get("cursor", {}).get("N", "0"))
            chunks = scan_pages or [list(records.values())]
            return {
                "Items": [encode(row) for row in chunks[index]],
                **({"LastEvaluatedKey": {"cursor": {"N": str(index + 1)}}}
                   if index + 1 < len(chunks) else {}),
            }

        async def get_item(self, **kwargs):
            mid = kwargs["Key"]["meetingId"]["S"]
            source = records if kwargs["TableName"] == "meetings" else pages
            return {"Item": encode(copy.deepcopy(source[mid]))} if mid in source else {}

        async def query(self, **kwargs):
            assert kwargs["TableName"] == "messages"
            mid = kwargs["ExpressionAttributeValues"][":m"]["S"]
            return {"Items": [encode(row) for row in messages.get(mid, [])]}

    class Body(io.BytesIO):
        async def read(self):
            return super().read()

    class S3:
        async def get_object(self, **kwargs):
            return {"Body": Body(objects[kwargs["Key"]])}

    class Events:
        async def iter_lines(self):
            yield "data: " + json.dumps({"phase": "done", "page": pages["m1"]})

    class Agent:
        async def invoke_agent_runtime(self, **kwargs):
            payload = json.loads(kwargs["payload"])
            payloads.append(payload)
            assert payload["meeting"]["style"] == "visual"
            previous = pages.get("m1")
            pages["m1"] = {
                "pageId": "new", "url": "https://pages/p/m1/new.html",
                "generatedAt": "2026-10-28T12:01:00+00:00",
            }
            objects["p/m1/new.html"] = VISUAL
            if previous:
                objects.pop(f"p/m1/{previous['pageId']}.html", None)
            return {"response": Events()}

    @asynccontextmanager
    async def ddb_client():
        yield DDB()

    @asynccontextmanager
    async def s3_client():
        yield S3()

    @asynccontextmanager
    async def agentcore_client():
        yield Agent()

    deps = PageAgentDeps(
        ddb_client=ddb_client, s3_client=s3_client, agentcore_client=agentcore_client,
        runtime_arn="test-runtime", bucket="pages", table="meetings", pages_table="pages",
        messages_table="messages",
    )
    return SimpleNamespace(
        deps=deps, records=records, messages=messages, pages=pages, objects=objects, payloads=payloads,
        scan_pages=scan_pages, scan_calls=scan_calls,
    )


async def test_selects_owner_and_exact_time_window_across_empty_scan_pages(env):
    env.scan_pages.extend([
        [meeting("old", createdAt="2026-10-14T11:59:59Z"),
         meeting("other", userId="u2")],
        [],
        [meeting("first", createdAt="2026-10-14T21:00:00+09:00"),
         meeting("last", createdAt="2026-10-28T12:00:00Z"),
         meeting("future", createdAt="2026-10-28T12:00:01Z")],
    ])
    async with env.deps.ddb_client() as ddb:
        selected = await backfill.recent_meetings(
            ddb, "meetings", since=SINCE, until=UNTIL, user_id="u1",
        )
    assert [row["meetingId"] for row in selected] == ["first", "last"]
    assert len(env.scan_calls) == 3


async def test_dry_run_does_not_invoke_generation_or_replace_a_page(env, tmp_path):
    env.pages["m1"] = {"pageId": "old", "url": "https://pages/p/m1/old.html"}
    env.objects["p/m1/old.html"] = LEGACY
    result = await backfill.backfill_meeting(
        "m1", deps=env.deps, user_id="u1", apply=False, output_dir=tmp_path,
    )
    assert result["status"] == "would_generate"
    assert env.pages["m1"]["pageId"] == "old"
    assert env.payloads == []


async def test_replaces_legacy_page_after_backing_up_original(env, tmp_path):
    env.pages["m1"] = {"pageId": "old", "url": "https://pages/p/m1/old.html"}
    env.objects["p/m1/old.html"] = LEGACY
    result = await backfill.backfill_meeting(
        "m1", deps=env.deps, user_id="u1", apply=True, output_dir=tmp_path,
    )
    assert result["status"] == "generated"
    assert result["url"] == "https://pages/p/m1/new.html"
    assert (tmp_path / "m1" / "before.html").read_bytes() == LEGACY
    assert json.loads((tmp_path / "m1" / "before.json").read_text())["pageId"] == "old"
    assert (tmp_path / "m1" / "after.html").read_bytes() == VISUAL
    assert env.payloads[0]["messages"][0]["original"] == "Ship on Friday."


async def test_generates_a_missing_page_and_can_be_rerun_without_regeneration(env, tmp_path):
    first = await backfill.backfill_meeting(
        "m1", deps=env.deps, user_id="u1", apply=True, output_dir=tmp_path,
    )
    second = await backfill.backfill_meeting(
        "m1", deps=env.deps, user_id="u1", apply=True, output_dir=tmp_path,
    )
    assert first["status"] == "generated"
    assert second["status"] == "already_visual"
    assert len(env.payloads) == 1


@pytest.mark.parametrize(("changes", "stored_messages", "expected"), [
    ({}, [], "empty"),
    ({}, [stored("  ")], "empty"),
    # 회의 항목에 남은 옛 messages 속성은 보지 않는다 — 메시지 테이블이 비면 빈 회의다
    ({"messages": [{"originalText": "Ship on Friday."}]}, [], "empty"),
    ({"userId": "u2"}, [stored("Ship on Friday.")], "outside_scope"),
])
async def test_skips_empty_meetings_and_rechecks_owner_before_writes(
    env, tmp_path, changes, stored_messages, expected,
):
    env.records["m1"].update(changes)
    env.messages["m1"] = stored_messages
    result = await backfill.backfill_meeting(
        "m1", deps=env.deps, user_id="u1", apply=True, output_dir=tmp_path,
    )
    assert result["status"] == expected
    assert env.payloads == []
    assert env.pages == {}


async def test_does_not_report_success_if_published_html_is_not_visual(env, tmp_path, monkeypatch):
    async def wrong_page(*args, **kwargs):
        env.pages["m1"] = {"pageId": "wrong", "url": "https://pages/p/m1/wrong.html"}
        env.objects["p/m1/wrong.html"] = LEGACY
        return env.pages["m1"]

    monkeypatch.setattr(backfill, "run_page_generation", wrong_page)
    result = await backfill.backfill_meeting(
        "m1", deps=env.deps, user_id="u1", apply=True, output_dir=tmp_path,
    )
    assert result["status"] == "failed"
    assert "visual" in result["error"]
