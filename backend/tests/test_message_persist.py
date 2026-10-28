"""ws_handler._persist_message — 메시지 테이블 트랜잭션 저장과 오류 처리."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from app import ws_handler

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
    _install(monkeypatch, FakeDDB(err))
    with caplog.at_level(logging.ERROR):
        await ws_handler._persist_message("m1", MESSAGE, log_tag="process_segment")
    assert any(
        r.levelno == logging.ERROR and "DynamoDB save failed" in r.getMessage()
        for r in caplog.records
    )


def test_no_list_append_left():
    assert "list_append" not in Path(ws_handler.__file__).read_text()
