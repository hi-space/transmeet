"""ws_handler._persist_message — 메시지 테이블 트랜잭션 저장과 오류 처리."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from app import message_store, ws_handler

MESSAGE = {
    "id": "msg-1",
    "speaker": "speaker1",
    "originalText": "Hello",
    "translatedText": "안녕",
    "detectedLanguage": "en",
    "timestamp": "2026-10-28T00:00:00+00:00",
}


class FakeDDB:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def transact_write_items(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    async def fake_sleep(s):
        return None

    monkeypatch.setattr(message_store, "_sleep", fake_sleep)


def _install(monkeypatch, ddb):
    @asynccontextmanager
    async def fake_client():
        yield ddb

    monkeypatch.setattr(ws_handler, "dynamodb_client", fake_client)


@pytest.mark.asyncio
async def test_persist_writes_transaction(monkeypatch):
    ddb = FakeDDB()
    _install(monkeypatch, ddb)
    await ws_handler._persist_message("m1", MESSAGE, log_tag="process_segment")
    assert len(ddb.calls) == 1


@pytest.mark.asyncio
async def test_persist_skips_deleted_meeting(monkeypatch, caplog):
    err = ClientError(
        {
            "Error": {"Code": "TransactionCanceledException"},
            "CancellationReasons": [{"Code": "None"}, {"Code": "ConditionalCheckFailed"}],
        },
        "TransactWriteItems",
    )
    _install(monkeypatch, FakeDDB(err))
    with caplog.at_level(logging.WARNING):
        await ws_handler._persist_message("m1", MESSAGE, log_tag="tts_request")
    assert any(
        r.levelno == logging.WARNING and "meeting does not exist" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_persist_logs_other_errors(monkeypatch, caplog):
    err = ClientError({"Error": {"Code": "ThrottlingException"}}, "TransactWriteItems")
    ddb = FakeDDB(err)
    _install(monkeypatch, ddb)
    with caplog.at_level(logging.ERROR):
        await ws_handler._persist_message("m1", MESSAGE, log_tag="process_segment")
    assert any(
        r.levelno == logging.ERROR and "DynamoDB save failed" in r.getMessage()
        for r in caplog.records
    )
    # 스로틀링은 append_message 가 첫 시도 + 4번 재시도한 뒤 포기한다
    assert len(ddb.calls) == 5


def test_no_list_append_left():
    assert "list_append" not in Path(ws_handler.__file__).read_text()


@pytest.mark.asyncio
async def test_persist_logs_unexpected_errors_with_message_id(monkeypatch, caplog):
    _install(monkeypatch, FakeDDB(RuntimeError("boom")))
    with caplog.at_level(logging.ERROR):
        # id 가 없는 메시지여도 로그 단계에서 KeyError 가 나지 않아야 한다
        await ws_handler._persist_message("m1", {k: v for k, v in MESSAGE.items() if k != "id"}, log_tag="x")
    assert any(
        r.levelno == logging.ERROR and "DynamoDB save failed" in r.getMessage()
        for r in caplog.records
    )


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


class _FakeStreamBedrock:
    async def converse_stream(self, **kw):
        async def events():
            yield {"contentBlockDelta": {"delta": {"text": "Hello there"}}}
        return {"stream": events()}


class _FakeAudio:
    async def read(self):
        return b"mp3"


class _FakePolly:
    async def synthesize_speech(self, **kw):
        return {"AudioStream": _FakeAudio()}


@pytest.mark.asyncio
async def test_tts_request_persists_mapped_message(monkeypatch):
    @asynccontextmanager
    async def fake_bedrock(*a, **kw):
        yield _FakeStreamBedrock()

    @asynccontextmanager
    async def fake_polly(*a, **kw):
        yield _FakePolly()

    saved = []

    async def fake_persist(meeting_id, message, *, log_tag):
        saved.append((meeting_id, message, log_tag))

    monkeypatch.setattr(ws_handler, "bedrock_client", fake_bedrock)
    monkeypatch.setattr(ws_handler, "polly_client", fake_polly)
    monkeypatch.setattr(ws_handler, "_persist_message", fake_persist)

    await ws_handler._handle_tts_request(
        _FakeWS(), "tts-1", "안녕하세요", "model", "neural", "Matthew",
        "2026-10-28T00:00:05+00:00", meeting_id="m1",
    )
    assert saved == [(
        "m1",
        {
            "id": "tts-1",
            "speaker": "me",
            "originalText": "Hello there",
            "translatedText": "안녕하세요",
            "detectedLanguage": "ko",
            "timestamp": "2026-10-28T00:00:05+00:00",
        },
        "tts_request",
    )]
    assert "targetLanguage" not in saved[0][1]
