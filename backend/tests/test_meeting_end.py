"""미팅 종료(endMeeting)와 녹음 다운로드(prepare_audio).

메일은 미팅당 한 번, 요약이 실패해도 오디오 링크와 함께 간다. 다운로드는 녹음 중이어도 그 순간까지 포함한다.
"""
import asyncio
from contextlib import asynccontextmanager

import pytest
from botocore.exceptions import ClientError

from app import ws_handler
from app.ws_handler import ConnectionState


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


class FakeDDB:
    """emailSentAt 조건부 쓰기만 흉내 낸다."""

    def __init__(self):
        self.item = {"title": {"S": "주간 1:1"}, "createdAt": {"S": "2026-10-28T00:00:00+00:00"}}

    async def get_item(self, **kw):
        return {"Item": dict(self.item)}

    async def update_item(self, **kw):
        expr = kw["UpdateExpression"]
        if "emailSentAt" in expr and expr.startswith("SET"):
            await asyncio.sleep(0)  # 동시 종료가 서로 끼어들 틈
            if "emailSentAt" in self.item:
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
            self.item["emailSentAt"] = {"S": "now"}
        elif expr.startswith("SET hasAudio"):
            self.item["hasAudio"] = {"BOOL": True}
        elif expr.startswith("REMOVE emailSentAt"):
            self.item.pop("emailSentAt", None)


@pytest.fixture
def env(monkeypatch):
    ddb = FakeDDB()
    calls = {"uploads": [], "merged": 0, "sent": [], "summaries": 0}
    summary = {"value": "## 결정\n- 출시"}
    verified = {"value": True}
    send_fails = {"value": False}

    @asynccontextmanager
    async def fake_ddb():
        yield ddb

    async def upload_part(meeting_id, conn_id, pcm, started_at=None):
        calls["uploads"].append((meeting_id, bytes(pcm)))
        calls.setdefault("started", []).append((bytes(pcm), started_at))
        return "k"

    async def ensure_merged(meeting_id):
        calls["merged"] += 1
        return f"meetings/{meeting_id}/audio.mp3" if calls["uploads"] else None

    async def presign(key, expires, filename):
        return f"https://signed/{key}?e={expires}"

    async def stream_summary(send, meeting_id, model_id, **kw):
        calls["summaries"] += 1
        return summary["value"]

    async def email_verified(email):
        return verified["value"]

    async def send_meeting_email(to, mail):
        if send_fails["value"]:
            raise RuntimeError("ses down")
        calls["sent"].append((to, mail))

    monkeypatch.setattr(ws_handler, "dynamodb_client", fake_ddb)
    monkeypatch.setattr(ws_handler.audio_store, "upload_part", upload_part)
    monkeypatch.setattr(ws_handler.audio_store, "ensure_merged", ensure_merged)
    monkeypatch.setattr(ws_handler.audio_store, "presign", presign)
    monkeypatch.setattr(ws_handler, "_stream_summary", stream_summary)
    monkeypatch.setattr(ws_handler.mailer, "email_verified", email_verified)
    monkeypatch.setattr(ws_handler.mailer, "send_meeting_email", send_meeting_email)
    monkeypatch.setattr(ws_handler, "active_connections", {})
    ws_handler._pending_uploads.clear()
    ws_handler._audio_marked.clear()
    return dict(ddb=ddb, calls=calls, summary=summary, verified=verified, send_fails=send_fails)


def make_state(meeting_id="m1", email="me@amazon.com"):
    ws = FakeWS()
    state = ConnectionState(ws=ws, user_id="u1", user_email=email)
    state.meeting_id = meeting_id
    ws_handler.active_connections["conn-1"] = state
    return state, ws


def events(ws, type_):
    return [e for e in ws.sent if e.get("type") == type_]


async def test_prepare_flushes_live_buffer(env):
    state, ws = make_state()
    ws_handler._buffer_audio(state, "conn-1", b"\x01\x00" * 100)
    await ws_handler._prepare_audio(ws_handler._meeting_broadcaster("m1"), "m1")
    assert env["calls"]["uploads"] == [("m1", b"\x01\x00" * 100)]
    ready = events(ws, "audio_ready")[-1]
    assert ready["url"].startswith("https://signed/meetings/m1/audio.mp3")
    assert "e=300" in ready["url"]


async def test_prepare_without_audio(env):
    state, ws = make_state()
    await ws_handler._prepare_audio(ws_handler._meeting_broadcaster("m1"), "m1")
    assert events(ws, "audio_ready")[-1]["error"] == "저장된 녹음이 없습니다"


async def test_end_meeting_sends_once(env):
    state, ws = make_state()
    ws_handler._buffer_audio(state, "conn-1", b"\x01\x00" * 10)
    send = ws_handler._meeting_broadcaster("m1")
    await asyncio.gather(
        ws_handler._end_meeting(send, state, "m1", email_on_end=True),
        ws_handler._end_meeting(send, state, "m1", email_on_end=True),
    )
    assert len(env["calls"]["sent"]) == 1
    to, mail = env["calls"]["sent"][0]
    assert to == "me@amazon.com"
    assert "https://signed/meetings/m1/audio.mp3?e=21600" in mail["html"]
    assert "https://transmeet.hi-yoo.com/?meeting=m1" in mail["html"]
    statuses = sorted(e["status"] for e in events(ws, "meeting_end") if e["phase"] == "email")
    assert statuses == ["already_sent", "sent"]


async def test_end_meeting_unverified(env):
    env["verified"]["value"] = False
    state, ws = make_state()
    await ws_handler._end_meeting(ws_handler._meeting_broadcaster("m1"), state, "m1", email_on_end=True)
    assert env["calls"]["sent"] == []
    assert [e["status"] for e in events(ws, "meeting_end") if e["phase"] == "email"] == ["unverified"]
    assert "emailSentAt" not in env["ddb"].item


async def test_end_meeting_summary_failure_still_emails(env):
    env["summary"]["value"] = None
    state, ws = make_state()
    await ws_handler._end_meeting(ws_handler._meeting_broadcaster("m1"), state, "m1", email_on_end=True)
    _, mail = env["calls"]["sent"][0]
    assert ws_handler.mailer.SUMMARY_FAILED_TEXT in mail["text"]
    assert events(ws, "meeting_end")[-1]["phase"] == "done"


async def test_end_meeting_send_failure_allows_retry(env):
    env["send_fails"]["value"] = True
    state, ws = make_state()
    send = ws_handler._meeting_broadcaster("m1")
    await ws_handler._end_meeting(send, state, "m1", email_on_end=True)
    assert "emailSentAt" not in env["ddb"].item
    env["send_fails"]["value"] = False
    await ws_handler._end_meeting(send, state, "m1", email_on_end=True)
    assert len(env["calls"]["sent"]) == 1


async def test_end_meeting_email_off(env):
    state, ws = make_state()
    await ws_handler._end_meeting(ws_handler._meeting_broadcaster("m1"), state, "m1", email_on_end=False)
    assert env["calls"]["sent"] == []
    assert env["calls"]["summaries"] == 1
    assert [e["status"] for e in events(ws, "meeting_end") if e["phase"] == "email"] == ["skipped"]


async def test_buffer_switches_meeting_flushes_old(env):
    state, ws = make_state()
    ws_handler._buffer_audio(state, "conn-1", b"\x01\x00")
    state.meeting_id = "m2"
    ws_handler._buffer_audio(state, "conn-1", b"\x02\x00")
    await ws_handler._flush_meeting_audio("m2")
    await ws_handler._await_uploads("m1")
    assert sorted(env["calls"]["uploads"]) == [("m1", b"\x01\x00"), ("m2", b"\x02\x00")]


async def test_late_flush_keeps_capture_order(env):
    """끊긴 연결의 구간이 재접속 후 구간보다 늦게 올라가도 키는 녹음 시작 시각을 따른다."""
    old, _ = make_state()
    ws_handler._buffer_audio(old, "conn-old", b"\x01\x00")
    await asyncio.sleep(0.001)
    new = ConnectionState(ws=FakeWS(), user_id="u1")
    new.meeting_id = "m1"
    ws_handler._buffer_audio(new, "conn-new", b"\x02\x00")
    ws_handler._start_flush(new, "conn-new")  # 새 연결이 먼저 올린다
    ws_handler._start_flush(old, "conn-old")  # ping 타임아웃 뒤에야 옛 연결이 올린다
    await ws_handler._await_uploads("m1")
    started = dict(env["calls"]["started"])
    assert started[b"\x01\x00"] < started[b"\x02\x00"]


async def test_upload_marks_has_audio(env):
    state, _ = make_state()
    ws_handler._buffer_audio(state, "conn-1", b"\x01\x00")
    await ws_handler._flush_meeting_audio("m1")
    assert env["ddb"].item["hasAudio"] == {"BOOL": True}
    assert "m1" not in ws_handler._pending_uploads  # 끝난 업로드 집합은 정리된다
