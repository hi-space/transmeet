import pytest
from botocore.exceptions import ClientError

from runtime.publish import PublishError, ddb_item_to_dict, page_key, publish_html

from validator_corpus import SECTIONS


def good_html(extra=""):
    secs = "".join(f'<section data-section="{s}"><p>해당 없음</p></section>' for s in SECTIONS)
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="transmeet:meeting-id" content="m1"><meta name="transmeet:generated-at" content="t">'
            f'<meta name="transmeet:model" content="x"></head><body>{secs}{extra}</body></html>')


class FakeS3:
    def __init__(self):
        self.objects, self.deleted = {}, []

    def put_object(self, **kw):
        self.objects[kw["Key"]] = kw
        return {}

    def delete_object(self, **kw):
        self.deleted.append(kw["Key"])
        self.objects.pop(kw["Key"], None)
        return {}


class FakeDDB:
    """미팅 테이블에 m1 하나, 페이지 테이블은 비어 있거나 page_id 기록 하나. TransactWriteItems 의
    ConditionCheck(미팅 존재) + Put(페이지) 를 흉내 낸다. 미팅 항목은 절대 쓰지 않는다."""

    def __init__(self, page_id=None, exists=True, fail=None):
        self.transacts, self.gets, self.exists, self.fail = [], [], exists, fail
        self.meetings = {"m1": {"meetingId": {"S": "m1"}}}
        self.pages = {"m1": {"meetingId": {"S": "m1"}, "pageId": {"S": page_id}}} if page_id else {}

    def get_item(self, **kw):
        self.gets.append(kw)
        table = self.pages if kw["TableName"] == "transmeet-pages" else self.meetings
        item = table.get(kw["Key"]["meetingId"]["S"])
        return {"Item": item} if item else {}

    def update_item(self, **kw):
        raise AssertionError("publish must not write to the meeting item")

    def transact_write_items(self, **kw):
        self.transacts.append(kw)
        if self.fail:
            raise self.fail
        items = kw["TransactItems"]
        check, put = items[0]["ConditionCheck"], items[1]["Put"]
        assert check["TableName"] == "transmeet-meetings" and put["TableName"] == "transmeet-pages"
        if not self.exists or check["Key"]["meetingId"]["S"] not in self.meetings:
            raise ClientError({"Error": {"Code": "TransactionCanceledException", "Message": "x"},
                               "CancellationReasons": [{"Code": "ConditionalCheckFailed"}, {"Code": "None"}]}, "TransactWriteItems")
        self.pages[put["Item"]["meetingId"]["S"]] = put["Item"]
        return {}


KW = dict(bucket="pages", base_url="https://transmeet.hi-yoo.com/p", table="transmeet-meetings", pages_table="transmeet-pages", max_bytes=1_500_000)


def pub(meeting_id, s3, ddb, html=None, title="배포 파이프라인", summary="파이프라인은 금요일까지. 알림은 다음 주.", **over):
    return publish_html(meeting_id, good_html() if html is None else html, title, summary, s3=s3, ddb=ddb, **{**KW, **over})


def test_publish_uploads_html_and_records_page():
    s3, ddb = FakeS3(), FakeDDB()
    page = pub("m1", s3, ddb)
    assert len(page.page_id) >= 16
    key = page_key("m1", page.page_id)
    assert key == f"p/m1/{page.page_id}.html"
    put = s3.objects[key]
    assert put["Bucket"] == "pages" and put["ContentType"] == "text/html; charset=utf-8"
    assert put["CacheControl"] == "no-cache"
    assert put["Body"].decode("utf-8") == good_html()
    assert page.url == f"https://transmeet.hi-yoo.com/p/m1/{page.page_id}.html"
    stored = ddb.pages["m1"]
    assert stored["meetingId"]["S"] == "m1" and stored["pageId"]["S"] == page.page_id
    assert stored["url"]["S"] == page.url and stored["title"]["S"] == "배포 파이프라인"
    assert stored["shareSummary"]["S"].startswith("파이프라인") and stored["generatedAt"]["S"] == page.generated_at
    assert page.to_item()["pageId"] == page.page_id and "meetingId" not in page.to_item()
    assert ddb.meetings["m1"] == {"meetingId": {"S": "m1"}}  # 미팅 항목은 그대로


def test_publish_twice_replaces_previous_object():
    s3, ddb = FakeS3(), FakeDDB()
    first = pub("m1", s3, ddb)
    second = pub("m1", s3, ddb)
    assert first.page_id != second.page_id
    assert page_key("m1", first.page_id) in s3.deleted
    assert list(s3.objects) == [page_key("m1", second.page_id)]
    assert ddb.pages["m1"]["pageId"]["S"] == second.page_id


def test_publish_rejects_invalid_html_without_side_effects():
    s3, ddb = FakeS3(), FakeDDB()
    with pytest.raises(PublishError) as ei:
        pub("m1", s3, ddb, html=good_html("<script>1</script>"))
    assert any("script" in e.lower() for e in ei.value.errors) and ei.value.transient is False
    assert not s3.objects and not ddb.transacts and not ddb.gets


def test_publish_rejects_missing_fields():
    with pytest.raises(PublishError) as ei:
        publish_html("m1", "", "t", "", s3=FakeS3(), ddb=FakeDDB(), **KW)
    assert any("html" in e for e in ei.value.errors)
    with pytest.raises(PublishError) as ei:
        pub("m1", FakeS3(), FakeDDB(), title="  ")
    assert any("title" in e for e in ei.value.errors)


def test_publish_size_limit_uses_max_bytes():
    with pytest.raises(PublishError) as ei:
        pub("m1", FakeS3(), FakeDDB(), max_bytes=10)
    assert any("bytes" in e for e in ei.value.errors)


def test_publish_deletes_previous_page_recorded_in_pages_table():
    s3, ddb = FakeS3(), FakeDDB(page_id="current")
    page = pub("m1", s3, ddb)
    check = ddb.transacts[0]["TransactItems"][0]["ConditionCheck"]
    assert check["ConditionExpression"] == "attribute_exists(meetingId)" and check["Key"] == {"meetingId": {"S": "m1"}}
    assert s3.deleted == [page_key("m1", "current")]
    assert list(s3.objects) == [page_key("m1", page.page_id)]


def test_publish_for_missing_meeting_rolls_back_upload():
    s3, ddb = FakeS3(), FakeDDB(exists=False)
    with pytest.raises(PublishError) as ei:
        pub("ghost", s3, ddb)
    assert ei.value.errors == ["meeting not found"]
    assert not s3.objects and len(s3.deleted) == 1 and s3.deleted[0].startswith("p/ghost/")


def test_publish_ddb_failure_rolls_back_upload_and_reraises():
    s3, ddb = FakeS3(), FakeDDB(fail=RuntimeError("ddb down"))
    with pytest.raises(RuntimeError, match="ddb down"):
        pub("m1", s3, ddb)
    assert not s3.objects and len(s3.deleted) == 1


def test_publish_other_transaction_cancellation_is_not_meeting_not_found():
    # 페이지 Put 쪽이 취소된 경우(예: 처리량)는 "meeting not found" 가 아니라 원래 예외로 올라간다
    err = ClientError({"Error": {"Code": "TransactionCanceledException", "Message": "x"},
                       "CancellationReasons": [{"Code": "None"}, {"Code": "ProvisionedThroughputExceeded"}]}, "TransactWriteItems")
    s3, ddb = FakeS3(), FakeDDB(fail=err)
    with pytest.raises(ClientError):
        pub("m1", s3, ddb)
    assert not s3.objects and len(s3.deleted) == 1


def test_ddb_item_to_dict():
    item = {"meetingId": {"S": "m1"}, "messages": {"L": [{"M": {"speaker": {"S": "me"}, "n": {"N": "2"}}}]},
            "page": {"M": {"pageId": {"S": "abc"}}}}
    d = ddb_item_to_dict(item)
    assert d["meetingId"] == "m1" and d["messages"][0]["speaker"] == "me" and d["messages"][0]["n"] == 2
    assert d["page"]["pageId"] == "abc"
