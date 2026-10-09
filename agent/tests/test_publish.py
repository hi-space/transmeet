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
    """meetingId=m1 항목 하나만 있는 테이블. ALL_OLD 와 attribute_exists 조건을 흉내 낸다."""

    def __init__(self, page_id=None, exists=True, fail=None):
        self.updates, self.exists, self.fail = [], exists, fail
        self.item = {"meetingId": {"S": "m1"}}
        if page_id:
            self.item["page"] = {"M": {"pageId": {"S": page_id}}}

    def update_item(self, **kw):
        self.updates.append(kw)
        if self.fail:
            raise self.fail
        if "attribute_exists(meetingId)" in kw.get("ConditionExpression", "") and not self.exists:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "UpdateItem")
        old = dict(self.item)
        if ":p" in kw.get("ExpressionAttributeValues", {}):
            self.item["page"] = kw["ExpressionAttributeValues"][":p"]
        return {"Attributes": old} if kw.get("ReturnValues") == "ALL_OLD" else {}


KW = dict(bucket="pages", base_url="https://transmeet.hi-yoo.com/p", table="transmeet-meetings", max_bytes=1_500_000)


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
    upd = ddb.updates[0]
    assert upd["TableName"] == "transmeet-meetings" and upd["Key"] == {"meetingId": {"S": "m1"}}
    assert "SET page = :p" in upd["UpdateExpression"]
    stored = upd["ExpressionAttributeValues"][":p"]["M"]
    assert stored["url"]["S"] == page.url and stored["title"]["S"] == "배포 파이프라인"
    assert stored["shareSummary"]["S"].startswith("파이프라인")
    assert page.to_item()["pageId"] == page.page_id


def test_publish_twice_replaces_previous_object():
    s3, ddb = FakeS3(), FakeDDB()
    first = pub("m1", s3, ddb)
    second = pub("m1", s3, ddb)
    assert first.page_id != second.page_id
    assert page_key("m1", first.page_id) in s3.deleted
    assert list(s3.objects) == [page_key("m1", second.page_id)]
    assert ddb.updates[-1]["ExpressionAttributeValues"][":p"]["M"]["pageId"]["S"] == second.page_id


def test_publish_rejects_invalid_html_without_side_effects():
    s3, ddb = FakeS3(), FakeDDB()
    with pytest.raises(PublishError) as ei:
        pub("m1", s3, ddb, html=good_html("<script>1</script>"))
    assert any("script" in e.lower() for e in ei.value.errors) and ei.value.transient is False
    assert not s3.objects and not ddb.updates


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


def test_publish_deletes_page_from_all_old_not_stale_value():
    s3, ddb = FakeS3(), FakeDDB(page_id="current")
    page = pub("m1", s3, ddb)
    upd = ddb.updates[0]
    assert upd["ReturnValues"] == "ALL_OLD" and upd["ConditionExpression"] == "attribute_exists(meetingId)"
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


def test_ddb_item_to_dict():
    item = {"meetingId": {"S": "m1"}, "messages": {"L": [{"M": {"speaker": {"S": "me"}, "n": {"N": "2"}}}]},
            "page": {"M": {"pageId": {"S": "abc"}}}}
    d = ddb_item_to_dict(item)
    assert d["meetingId"] == "m1" and d["messages"][0]["speaker"] == "me" and d["messages"][0]["n"] == 2
    assert d["page"]["pageId"] == "abc"
