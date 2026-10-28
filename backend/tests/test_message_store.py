import asyncio

from botocore.exceptions import ClientError

from app import message_store
from app.config import config


def _msg(i, ts="2026-10-28T00:00:01.000000+00:00"):
    return {
        "id": f"m-{i}",
        "speaker": "A",
        "originalText": f"o{i}",
        "translatedText": f"t{i}",
        "detectedLanguage": "ko",
        "timestamp": ts,
    }


def _item(i):
    m = _msg(i)
    item = {k: {"S": v} for k, v in m.items()}
    item["meetingId"] = {"S": "mtg"}
    item["sk"] = {"S": message_store.sort_key(m["timestamp"], m["id"])}
    return item


class FakeDdb:
    def __init__(self, pages=None):
        self.transactions = []
        self.queries = []
        self.pages = list(pages or [])

    async def transact_write_items(self, **kwargs):
        self.transactions.append(kwargs)
        return {}

    async def query(self, **kwargs):
        self.queries.append(kwargs)
        return self.pages[len(self.queries) - 1]


def run(coro):
    return asyncio.run(coro)


def test_append_message_writes_put_and_counter():
    ddb = FakeDdb()
    run(message_store.append_message(ddb, "mtg", _msg(1), updated_at="2026-10-28T00:00:02+00:00"))
    items = ddb.transactions[0]["TransactItems"]
    put = items[0]["Put"]
    assert put["TableName"] == config.MESSAGES_TABLE
    assert put["Item"]["sk"]["S"] == "2026-10-28T00:00:01.000000+00:00#m-1"
    assert put["Item"]["meetingId"]["S"] == "mtg"
    upd = items[1]["Update"]
    assert upd["TableName"] == config.MEETINGS_TABLE
    assert upd["ConditionExpression"] == "attribute_exists(meetingId)"
    assert "ADD messageCount :one" in upd["UpdateExpression"]


def test_same_timestamp_messages_do_not_collide():
    ddb = FakeDdb()
    run(message_store.append_message(ddb, "mtg", _msg(1), updated_at="x"))
    run(message_store.append_message(ddb, "mtg", _msg(2), updated_at="x"))
    sks = [t["TransactItems"][0]["Put"]["Item"]["sk"]["S"] for t in ddb.transactions]
    assert sks[0] != sks[1]


def _err(code, reasons=None):
    resp = {"Error": {"Code": code, "Message": "x"}}
    if reasons is not None:
        resp["CancellationReasons"] = reasons
    return ClientError(resp, "TransactWriteItems")


def test_is_missing_meeting():
    assert message_store.is_missing_meeting(
        _err("TransactionCanceledException", [{"Code": "None"}, {"Code": "ConditionalCheckFailed"}])
    )
    assert not message_store.is_missing_meeting(
        _err("TransactionCanceledException", [{"Code": "ThrottlingError"}, {"Code": "None"}])
    )
    assert not message_store.is_missing_meeting(
        _err("ProvisionedThroughputExceededException", [{"Code": "ConditionalCheckFailed"}])
    )


def test_load_messages_follows_pages():
    ddb = FakeDdb([
        {"Items": [_item(1), _item(2)], "LastEvaluatedKey": {"sk": {"S": "k"}}},
        {"Items": [_item(3), _item(4)]},
    ])
    out = run(message_store.load_messages(ddb, "mtg"))
    assert [m["id"] for m in out] == ["m-1", "m-2", "m-3", "m-4"]
    assert set(out[0]) == {"id", "speaker", "originalText", "translatedText", "detectedLanguage", "timestamp"}
    assert len(ddb.queries) == 2
    assert all(q["ConsistentRead"] is True for q in ddb.queries)
    assert all(q.get("ScanIndexForward", True) is True for q in ddb.queries)
    assert ddb.queries[1]["ExclusiveStartKey"] == {"sk": {"S": "k"}}
    assert ddb.queries[0]["TableName"] == config.MESSAGES_TABLE


def test_load_messages_limit():
    ddb = FakeDdb([
        {"Items": [_item(1), _item(2)], "LastEvaluatedKey": {"sk": {"S": "k"}}},
        {"Items": [_item(3), _item(4)], "LastEvaluatedKey": {"sk": {"S": "k2"}}},
        {"Items": [_item(5)]},
    ])
    out = run(message_store.load_messages(ddb, "mtg", limit=3))
    assert [m["id"] for m in out] == ["m-1", "m-2", "m-3"]
    assert len(ddb.queries) == 2


def test_load_messages_custom_table_and_missing_attrs():
    ddb = FakeDdb([{"Items": [{"id": {"S": "z"}}]}])
    out = run(message_store.load_messages(ddb, "mtg", table="other"))
    assert ddb.queries[0]["TableName"] == "other"
    assert out == [{"id": "z", "speaker": "", "originalText": "", "translatedText": "",
                    "detectedLanguage": "", "timestamp": ""}]


def test_append_message_keeps_target_language():
    ddb = FakeDdb()
    run(message_store.append_message(ddb, "mtg", {**_msg(1), "targetLanguage": "en"}, updated_at="t"))
    run(message_store.append_message(ddb, "mtg", {**_msg(2), "targetLanguage": ""}, updated_at="t"))
    with_lang = ddb.transactions[0]["TransactItems"][0]["Put"]["Item"]
    without = ddb.transactions[1]["TransactItems"][0]["Put"]["Item"]
    assert with_lang["targetLanguage"] == {"S": "en"}
    assert "targetLanguage" not in without


def test_load_messages_returns_target_language_when_present():
    has = {**_item(1), "targetLanguage": {"S": "ko"}}
    ddb = FakeDdb([{"Items": [has, _item(2)]}])
    out = run(message_store.load_messages(ddb, "mtg"))
    assert out[0]["targetLanguage"] == "ko"
    assert "targetLanguage" not in out[1]


class FlakyDdb(FakeDdb):
    """앞의 몇 번은 정해 둔 예외로 실패하고, 그 뒤에는 성공한다."""

    def __init__(self, errors):
        super().__init__()
        self.errors = list(errors)
        self.attempts = 0

    async def transact_write_items(self, **kwargs):
        self.attempts += 1
        if self.errors:
            raise self.errors.pop(0)
        return await super().transact_write_items(**kwargs)


def _conflict():
    return _err("TransactionCanceledException", [{"Code": "TransactionConflict"}, {"Code": "None"}])


def _no_sleep(monkeypatch):
    delays = []

    async def fake_sleep(s):
        delays.append(s)

    monkeypatch.setattr(message_store, "_sleep", fake_sleep)
    return delays


def test_is_retryable_cancellation():
    assert message_store.is_retryable_cancellation(_conflict())
    assert message_store.is_retryable_cancellation(
        _err("TransactionCanceledException", [{"Code": "None"}, {"Code": "ThrottlingError"}])
    )
    assert message_store.is_retryable_cancellation(_err("ThrottlingException"))
    assert message_store.is_retryable_cancellation(_err("ProvisionedThroughputExceededException"))
    assert not message_store.is_retryable_cancellation(
        _err("TransactionCanceledException", [{"Code": "None"}, {"Code": "ConditionalCheckFailed"}])
    )
    assert not message_store.is_retryable_cancellation(_err("ValidationException"))


def test_append_message_retries_conflict_then_succeeds(monkeypatch):
    delays = _no_sleep(monkeypatch)
    ddb = FlakyDdb([_conflict()])
    run(message_store.append_message(ddb, "mtg", _msg(1), updated_at="t"))
    assert ddb.attempts == 2
    assert len(ddb.transactions) == 1  # 한 건만 써졌다
    assert len(delays) == 1


def test_append_message_gives_up_after_four_retries(monkeypatch):
    delays = _no_sleep(monkeypatch)
    ddb = FlakyDdb([_conflict() for _ in range(5)])
    try:
        run(message_store.append_message(ddb, "mtg", _msg(1), updated_at="t"))
    except ClientError as err:
        assert message_store.is_retryable_cancellation(err)
    else:
        raise AssertionError("재시도가 끝나면 예외를 던져야 한다")
    assert ddb.attempts == 5
    assert ddb.transactions == []
    assert len(delays) == 4
    # 간격은 시도마다 늘어난다(지터 포함해도 단조 증가하도록 상한을 둔다)
    assert all(d > 0 for d in delays)


def test_append_message_does_not_retry_missing_meeting(monkeypatch):
    delays = _no_sleep(monkeypatch)
    ddb = FlakyDdb([_err("TransactionCanceledException", [{"Code": "None"}, {"Code": "ConditionalCheckFailed"}])])
    try:
        run(message_store.append_message(ddb, "mtg", _msg(1), updated_at="t"))
    except ClientError as err:
        assert message_store.is_missing_meeting(err)
    else:
        raise AssertionError("회의 없음은 바로 던져야 한다")
    assert ddb.attempts == 1
    assert delays == []
