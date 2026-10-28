"""회의 대화 이전 스크립트(copy·verify·remove) 테스트. AWS 호출 없이 메모리 가짜 클라이언트로 돈다."""
import pytest
from botocore.exceptions import ClientError

from app.config import config
from app.message_store import sort_key
from scripts import migrate_messages as mm

WRITE_OPS = {"batch_write_item", "update_item", "put_item", "delete_item", "transact_write_items"}


def _msg(i, ts=None, **extra):
    m = {
        "id": {"S": f"m{i}"},
        "speaker": {"S": "A"},
        "originalText": {"S": f"hello {i}"},
        "translatedText": {"S": f"안녕 {i}"},
        "detectedLanguage": {"S": "en"},
        "timestamp": {"S": ts or f"2026-10-28T00:00:{i:02d}.000000+00:00"},
    }
    m.update(extra)
    return {"M": m}


def _meeting(mid, n, **extra):
    item = {"meetingId": {"S": mid}, "messages": {"L": [_msg(i) for i in range(n)]}}
    item.update(extra)
    return item


class FakeDdb:
    """boto3 저수준 클라이언트 흉내. 스캔·쿼리는 작은 페이지로 나눠 페이지 이어 받기를 검사한다."""

    def __init__(self, meetings=(), *, page=2, unprocessed_rounds=0):
        self.meetings = {m["meetingId"]["S"]: m for m in meetings}
        self.messages = {}  # meetingId -> {sk: item}
        self.page = page
        self.unprocessed_rounds = unprocessed_rounds
        self.calls = []

    def _log(self, op, kw):
        self.calls.append((op, kw))

    def writes(self):
        return [c for c in self.calls if c[0] in WRITE_OPS]

    def scan(self, **kw):
        self._log("scan", kw)
        if kw["TableName"] == config.MESSAGES_TABLE:
            return self._scan_messages(kw)
        assert kw["TableName"] == config.MEETINGS_TABLE
        assert kw["ProjectionExpression"] == mm.PROJECTION
        ids = sorted(self.meetings)
        start = ids.index(kw["ExclusiveStartKey"]["meetingId"]["S"]) + 1 if "ExclusiveStartKey" in kw else 0
        chunk = ids[start:start + self.page]
        resp = {"Items": [self._project(self.meetings[i]) for i in chunk]}
        if start + self.page < len(ids):
            resp["LastEvaluatedKey"] = {"meetingId": {"S": chunk[-1]}}
        return resp

    def _scan_messages(self, kw):
        assert kw["ProjectionExpression"] == "meetingId, sk"
        rows = [
            {"meetingId": {"S": mid}, "sk": {"S": sk}}
            for mid in sorted(self.messages) for sk in sorted(self.messages[mid])
        ]
        start = 0
        if "ExclusiveStartKey" in kw:
            start = rows.index(kw["ExclusiveStartKey"]) + 1
        chunk = rows[start:start + self.page]
        resp = {"Items": chunk}
        if start + self.page < len(rows):
            resp["LastEvaluatedKey"] = chunk[-1]
        return resp

    @staticmethod
    def _project(item):
        return {k: v for k, v in item.items() if k in ("meetingId", "messages", "messageCount")}

    def get_item(self, **kw):
        self._log("get_item", kw)
        item = self.meetings.get(kw["Key"]["meetingId"]["S"])
        return {"Item": self._project(item)} if item else {}

    def query(self, **kw):
        self._log("query", kw)
        assert kw["TableName"] == config.MESSAGES_TABLE
        assert kw["ConsistentRead"] is True
        mid = kw["ExpressionAttributeValues"][":m"]["S"]
        rows = [v for _, v in sorted(self.messages.get(mid, {}).items())]
        start = 0
        if "ExclusiveStartKey" in kw:
            sks = [r["sk"]["S"] for r in rows]
            start = sks.index(kw["ExclusiveStartKey"]["sk"]["S"]) + 1
        chunk = rows[start:start + self.page]
        resp = {"Items": chunk}
        if start + self.page < len(rows):
            resp["LastEvaluatedKey"] = {"meetingId": {"S": mid}, "sk": chunk[-1]["sk"]}
        return resp

    def batch_write_item(self, **kw):
        self._log("batch_write_item", kw)
        reqs = kw["RequestItems"][config.MESSAGES_TABLE]
        assert 1 <= len(reqs) <= 25
        if self.unprocessed_rounds > 0:
            # 앞 절반만 처리하고 나머지는 미처리로 돌려준다
            self.unprocessed_rounds -= 1
            done, rest = reqs[: len(reqs) // 2], reqs[len(reqs) // 2:]
        else:
            done, rest = reqs, []
        for r in done:
            if "DeleteRequest" in r:
                key = r["DeleteRequest"]["Key"]
                assert set(key) == {"meetingId", "sk"}
                rows = self.messages.get(key["meetingId"]["S"], {})
                rows.pop(key["sk"]["S"], None)
                if not rows:
                    self.messages.pop(key["meetingId"]["S"], None)
                continue
            item = r["PutRequest"]["Item"]
            self.messages.setdefault(item["meetingId"]["S"], {})[item["sk"]["S"]] = item
        return {"UnprocessedItems": {config.MESSAGES_TABLE: rest} if rest else {}}

    def update_item(self, **kw):
        self._log("update_item", kw)
        assert kw["TableName"] == config.MEETINGS_TABLE
        item = self.meetings[kw["Key"]["meetingId"]["S"]]
        n = int(kw["ExpressionAttributeValues"][":n"]["N"])
        assert kw["ConditionExpression"] == "size(messages) = :n"
        if "messages" not in item or len(item["messages"]["L"]) != n:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "failed"}}, "UpdateItem"
            )
        assert kw["UpdateExpression"] == "REMOVE messages"
        del item["messages"]
        return {}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(mm, "_sleep", lambda s: None)


def test_copy_is_idempotent():
    meeting = _meeting("a", 30)
    ddb = FakeDdb([meeting])
    assert mm.copy_meeting(ddb, meeting) == 30
    assert mm.copy_meeting(ddb, meeting) == 30
    assert len(ddb.messages["a"]) == 30
    # 25개씩 나눠 쓴다
    sizes = [len(kw["RequestItems"][config.MESSAGES_TABLE]) for op, kw in ddb.writes()]
    assert sizes == [25, 5, 25, 5]


def test_copy_keeps_all_attributes_and_adds_keys():
    meeting = {"meetingId": {"S": "a"}, "messages": {"L": [_msg(1, targetLanguage={"S": "ko"})]}}
    ddb = FakeDdb([meeting])
    mm.copy_meeting(ddb, meeting)
    (item,) = ddb.messages["a"].values()
    original = meeting["messages"]["L"][0]["M"]
    assert item == {**original, "meetingId": {"S": "a"}, "sk": {"S": sort_key(original["timestamp"]["S"], "m1")}}


def test_copy_retries_unprocessed_items():
    meeting = _meeting("a", 10)
    ddb = FakeDdb([meeting], unprocessed_rounds=2)
    assert mm.copy_meeting(ddb, meeting) == 10
    assert len(ddb.messages["a"]) == 10
    assert len(ddb.writes()) == 3


def test_copy_raises_when_unprocessed_never_clears():
    meeting = _meeting("a", 10)
    ddb = FakeDdb([meeting], unprocessed_rounds=10**6)
    with pytest.raises(RuntimeError):
        mm.copy_meeting(ddb, meeting)


def test_verify_detects_missing():
    meeting = _meeting("a", 3)
    ddb = FakeDdb([meeting])
    mm.copy_meeting(ddb, meeting)
    ddb.messages["a"].pop(sorted(ddb.messages["a"])[1])
    assert mm.verify_meeting(ddb, meeting) == (False, 3, 2)
    # 테이블에 다른 메시지가 더 있어도 원본 id 가 빠지면 불일치
    extra = mm._to_item("a", _msg(7)["M"])
    ddb.messages["a"][extra["sk"]["S"]] = extra
    assert mm.verify_meeting(ddb, meeting) == (False, 3, 3)


def test_verify_allows_extra_table_messages():
    meeting = _meeting("a", 3)
    ddb = FakeDdb([meeting])
    mm.copy_meeting(ddb, meeting)
    # 전환 뒤 새 코드는 테이블에만 쓴다
    extra = mm._to_item("a", _msg(3)["M"])
    ddb.messages["a"][extra["sk"]["S"]] = extra
    assert mm.verify_meeting(ddb, meeting) == (True, 3, 4)


def test_verify_warns_on_message_count_mismatch():
    good = _meeting("good", 2, messageCount={"N": "2"})
    off = _meeting("off", 2, messageCount={"N": "5"})
    missing = _meeting("missing", 1)
    ddb = FakeDdb([good, off, missing])
    for m in (good, off, missing):
        mm.copy_meeting(ddb, m)
    lines = []
    summary = mm.run(ddb, "verify", out=lambda *a: lines.append(" ".join(map(str, a))))
    assert summary["message_count_mismatch_ids"] == ["missing", "off"]
    assert summary["matched"] == 3 and summary["mismatched_ids"] == []
    assert any("messageCount 불일치" in line for line in lines)
    # 경고일 뿐 remove 를 막지 않는다
    summary = mm.run(ddb, "remove", execute=True, out=lambda *a: None)
    assert summary["removed"] == 3
    assert summary["message_count_mismatch_ids"] == ["missing", "off"]


def test_verify_matches_across_pages():
    meeting = _meeting("a", 7)
    ddb = FakeDdb([meeting], page=2)
    mm.copy_meeting(ddb, meeting)
    assert mm.verify_meeting(ddb, meeting) == (True, 7, 7)


def test_verify_detects_different_ids_with_same_count():
    meeting = _meeting("a", 3)
    ddb = FakeDdb([meeting])
    mm.copy_meeting(ddb, meeting)
    sk = sorted(ddb.messages["a"])[0]
    ddb.messages["a"][sk] = {**ddb.messages["a"][sk], "id": {"S": "other"}}
    assert mm.verify_meeting(ddb, meeting) == (False, 3, 3)


def test_verify_treats_missing_list_as_zero():
    meeting = {"meetingId": {"S": "a"}}
    assert mm.verify_meeting(FakeDdb([meeting]), meeting) == (True, 0, 0)


def test_remove_skips_when_list_grew():
    meeting = _meeting("a", 3)
    ddb = FakeDdb([meeting])
    # 검증 뒤 옛 코드가 한 줄을 더 붙였다
    meeting["messages"]["L"].append(_msg(3))
    assert mm.remove_meeting(ddb, "a", 3) is False
    assert len(ddb.meetings["a"]["messages"]["L"]) == 4


def test_remove_does_not_touch_message_count():
    meeting = _meeting("a", 3, messageCount={"N": "4"})
    ddb = FakeDdb([meeting])
    assert mm.remove_meeting(ddb, "a", 3) is True
    (op, kw), = ddb.writes()
    assert kw["UpdateExpression"] == "REMOVE messages"
    assert "messageCount" not in kw["UpdateExpression"]
    assert kw["ConditionExpression"] == "size(messages) = :n"
    assert kw["ExpressionAttributeValues"] == {":n": {"N": "3"}}
    assert "messages" not in ddb.meetings["a"]
    assert ddb.meetings["a"]["messageCount"] == {"N": "4"}


def test_remove_raises_other_errors():
    class Boom(FakeDdb):
        def update_item(self, **kw):
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem")

    with pytest.raises(ClientError):
        mm.remove_meeting(Boom([_meeting("a", 1)]), "a", 1)


def test_dry_run_writes_nothing():
    ddb = FakeDdb([_meeting("a", 3), _meeting("b", 30), {"meetingId": {"S": "c"}}])
    for command in ("copy", "verify", "remove"):
        mm.run(ddb, command, execute=False, out=lambda *a: None)
    assert ddb.writes() == []
    assert ddb.messages == {}


def test_scan_follows_pages():
    ddb = FakeDdb([_meeting(f"m{i}", 1) for i in range(5)], page=2)
    summary = mm.run(ddb, "copy", execute=True, out=lambda *a: None)
    assert summary["processed"] == 5
    assert set(ddb.messages) == {f"m{i}" for i in range(5)}
    scans = [kw for op, kw in ddb.calls if op == "scan"]
    assert len(scans) == 3


def test_meeting_id_restricts_to_one():
    ddb = FakeDdb([_meeting("a", 2), _meeting("b", 2)])
    summary = mm.run(ddb, "copy", execute=True, meeting_id="b", out=lambda *a: None)
    assert summary["processed"] == 1
    assert set(ddb.messages) == {"b"}
    assert not [c for c in ddb.calls if c[0] == "scan"]


def test_run_remove_only_verified_meetings():
    ok, bad, empty = _meeting("ok", 3), _meeting("bad", 3), {"meetingId": {"S": "empty"}}
    ddb = FakeDdb([ok, bad, empty])
    mm.copy_meeting(ddb, ok)
    mm.copy_meeting(ddb, bad)
    ddb.messages["bad"].pop(sorted(ddb.messages["bad"])[0])
    summary = mm.run(ddb, "remove", execute=True, out=lambda *a: None)
    assert "messages" not in ddb.meetings["ok"]
    assert len(ddb.meetings["bad"]["messages"]["L"]) == 3
    assert summary["matched"] == 2  # ok, empty
    assert summary["mismatched_ids"] == ["bad"]
    assert summary["no_messages_ids"] == ["empty"]
    assert summary["removed"] == 1
    updates = [kw["Key"]["meetingId"]["S"] for op, kw in ddb.writes() if op == "update_item"]
    assert updates == ["ok"]


def test_run_remove_reports_condition_failure(monkeypatch):
    meeting = _meeting("a", 3)
    ddb = FakeDdb([meeting])
    mm.copy_meeting(ddb, meeting)

    real_verify = mm.verify_meeting

    def verify_then_grow(client, m):
        result = real_verify(client, m)
        ddb.meetings["a"]["messages"]["L"].append(_msg(9))  # 검증 직후 한 줄이 붙는다
        return result

    monkeypatch.setattr(mm, "verify_meeting", verify_then_grow)
    summary = mm.run(ddb, "remove", execute=True, out=lambda *a: None)
    assert summary["condition_failed_ids"] == ["a"]
    assert summary["removed"] == 0
    assert len(ddb.meetings["a"]["messages"]["L"]) == 4


class ConflictOnRemove(FakeDdb):
    """REMOVE 가 진행 중인 트랜잭션과 겹쳐 처음 몇 번은 TransactionConflictException."""

    def __init__(self, meetings, conflicts):
        super().__init__(meetings)
        self.conflicts = conflicts
        self.update_attempts = 0

    def update_item(self, **kw):
        self.update_attempts += 1
        if self.conflicts > 0:
            self.conflicts -= 1
            raise ClientError({"Error": {"Code": "TransactionConflictException"}}, "UpdateItem")
        return super().update_item(**kw)


def test_remove_retries_transaction_conflict(monkeypatch):
    delays = []
    monkeypatch.setattr(mm, "_sleep", delays.append)
    ddb = ConflictOnRemove([_meeting("a", 2)], conflicts=2)
    assert mm.remove_meeting(ddb, "a", 2) is True
    assert ddb.update_attempts == 3
    assert len(delays) == 2
    assert "messages" not in ddb.meetings["a"]


def test_remove_raises_after_four_conflict_retries(monkeypatch):
    delays = []
    monkeypatch.setattr(mm, "_sleep", delays.append)
    ddb = ConflictOnRemove([_meeting("a", 2)], conflicts=5)
    with pytest.raises(ClientError):
        mm.remove_meeting(ddb, "a", 2)
    assert ddb.update_attempts == 5
    assert len(delays) == 4
    assert len(ddb.meetings["a"]["messages"]["L"]) == 2


def _orphan_setup():
    """회의 live 는 남아 있고, gone 은 회의 항목만 지워져 메시지 30건이 고아로 남았다."""
    live, gone = _meeting("live", 3), _meeting("gone", 30)
    ddb = FakeDdb([live, gone], page=4)
    mm.copy_meeting(ddb, live)
    mm.copy_meeting(ddb, gone)
    del ddb.meetings["gone"]  # 옛 삭제 Lambda 는 회의 항목만 지웠다
    ddb.calls.clear()
    return ddb


def test_orphans_dry_run_reports_and_writes_nothing():
    ddb = _orphan_setup()
    lines = []
    summary = mm.run_orphans(ddb, execute=False, out=lambda *a: lines.append(" ".join(map(str, a))))
    assert summary["orphans"] == {"gone": 30}
    assert summary["deleted"] == 0
    assert ddb.writes() == []
    assert len(ddb.messages["gone"]) == 30
    assert any("gone" in line and "30" in line for line in lines)
    # 메시지 테이블 스캔은 키만, 페이지를 끝까지 따라간다
    scans = [kw for op, kw in ddb.calls if op == "scan"]
    assert all(kw["TableName"] == config.MESSAGES_TABLE for kw in scans)
    assert len(scans) == 9  # 33건 / 4
    # 회의 존재 확인은 강한 일관성
    gets = [kw for op, kw in ddb.calls if op == "get_item"]
    assert gets and all(kw["ConsistentRead"] is True for kw in gets)


def test_orphans_execute_deletes_only_orphans():
    ddb = _orphan_setup()
    summary = mm.run_orphans(ddb, execute=True, out=lambda *a: None)
    assert summary["orphans"] == {"gone": 30}
    assert summary["deleted"] == 30
    assert "gone" not in ddb.messages
    assert len(ddb.messages["live"]) == 3
    sizes = [len(kw["RequestItems"][config.MESSAGES_TABLE]) for op, kw in ddb.writes()]
    assert sizes == [25, 5]
    # 다시 돌리면 고아가 없다
    assert mm.run_orphans(ddb, execute=False, out=lambda *a: None)["orphans"] == {}


def test_orphans_execute_retries_unprocessed_and_raises_on_leftovers():
    ddb = _orphan_setup()
    ddb.unprocessed_rounds = 2
    assert mm.run_orphans(ddb, execute=True, out=lambda *a: None)["deleted"] == 30
    assert "gone" not in ddb.messages

    ddb = _orphan_setup()
    ddb.unprocessed_rounds = 10**6
    with pytest.raises(RuntimeError):
        mm.run_orphans(ddb, execute=True, out=lambda *a: None)
    assert len(ddb.messages["live"]) == 3


def test_orphans_cli_dispatch(monkeypatch):
    ddb = _orphan_setup()
    monkeypatch.setattr(mm.boto3, "client", lambda *a, **kw: ddb)
    monkeypatch.setattr("builtins.print", lambda *a, **kw: None)
    assert mm.main(["orphans"]) == 0
    assert ddb.writes() == []
    assert mm.main(["orphans", "--execute"]) == 0
    assert "gone" not in ddb.messages and len(ddb.messages["live"]) == 3
