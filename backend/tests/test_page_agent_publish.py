import pytest

from app.page_agent import (
    PublishValidationError,
    ddb_item_to_dict,
    page_key,
    publish_page,
    unpublish_page,
    validate_html,
)

SECTIONS = ["header", "tldr", "decisions", "action-items", "discussion", "open-questions", "references", "insights", "glossary", "footer"]


def good_html(extra=""):
    secs = "".join(f'<section data-section="{s}"><p>해당 없음</p></section>' for s in SECTIONS)
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="transmeet:meeting-id" content="m1"><meta name="transmeet:generated-at" content="t">'
            f'<meta name="transmeet:model" content="x"></head><body>{secs}{extra}</body></html>')


class FakeS3:
    def __init__(self):
        self.objects, self.deleted = {}, []

    async def put_object(self, **kw):
        self.objects[kw["Key"]] = kw
        return {}

    async def delete_object(self, **kw):
        self.deleted.append(kw["Key"])
        self.objects.pop(kw["Key"], None)
        return {}


class FakeDDB:
    def __init__(self):
        self.updates = []

    async def update_item(self, **kw):
        self.updates.append(kw)
        return {}


ARGS = {"title": "배포 파이프라인", "html": good_html(), "share_summary": "파이프라인은 금요일까지. 알림은 다음 주."}


def test_validate_html_rejects_event_handlers_and_javascript_href():
    errs = validate_html(good_html('<a href="javascript:alert(1)" onclick="x()">x</a>'), 1_500_000)
    assert any("javascript:" in e for e in errs) and any("onclick" in e for e in errs)
    assert validate_html(good_html(), 1_500_000) == []


def test_validate_html_size_and_sections():
    assert any("bytes" in e for e in validate_html(good_html(), 10))
    assert any("glossary" in e for e in validate_html(good_html().replace('data-section="glossary"', ""), 1_500_000))


async def test_publish_uploads_html_and_records_page():
    s3, ddb = FakeS3(), FakeDDB()
    page = await publish_page("m1", ARGS, s3=s3, ddb=ddb, bucket="pages", base_url="https://transmeet.hi-yoo.com/p",
                              table="transmeet-meetings", max_bytes=1_500_000)
    assert len(page.page_id) >= 16
    key = page_key("m1", page.page_id)
    assert key == f"p/m1/{page.page_id}.html"
    put = s3.objects[key]
    assert put["Bucket"] == "pages" and put["ContentType"] == "text/html; charset=utf-8"
    assert put["CacheControl"] == "no-cache"
    assert put["Body"].decode("utf-8") == ARGS["html"]
    assert page.url == f"https://transmeet.hi-yoo.com/p/m1/{page.page_id}.html"
    upd = ddb.updates[0]
    assert upd["TableName"] == "transmeet-meetings" and upd["Key"] == {"meetingId": {"S": "m1"}}
    assert "SET page = :p" in upd["UpdateExpression"]
    stored = upd["ExpressionAttributeValues"][":p"]["M"]
    assert stored["url"]["S"] == page.url and stored["title"]["S"] == "배포 파이프라인"
    assert stored["shareSummary"]["S"].startswith("파이프라인")
    assert page.to_item()["pageId"] == page.page_id


async def test_publish_twice_replaces_previous_object():
    s3, ddb = FakeS3(), FakeDDB()
    first = await publish_page("m1", ARGS, s3=s3, ddb=ddb, bucket="pages", base_url="https://x/p", table="t", max_bytes=1_500_000)
    second = await publish_page("m1", ARGS, s3=s3, ddb=ddb, bucket="pages", base_url="https://x/p", table="t",
                                previous_page_id=first.page_id, max_bytes=1_500_000)
    assert first.page_id != second.page_id
    assert page_key("m1", first.page_id) in s3.deleted
    assert list(s3.objects) == [page_key("m1", second.page_id)]
    assert ddb.updates[-1]["ExpressionAttributeValues"][":p"]["M"]["pageId"]["S"] == second.page_id


async def test_publish_rejects_invalid_html_without_side_effects():
    s3, ddb = FakeS3(), FakeDDB()
    with pytest.raises(PublishValidationError) as ei:
        await publish_page("m1", {**ARGS, "html": good_html("<script>1</script>")}, s3=s3, ddb=ddb, bucket="b",
                           base_url="https://x/p", table="t", max_bytes=1_500_000)
    assert any("script" in e.lower() for e in ei.value.errors)
    assert not s3.objects and not ddb.updates


async def test_publish_rejects_missing_fields():
    with pytest.raises(PublishValidationError) as ei:
        await publish_page("m1", {"title": "t"}, s3=FakeS3(), ddb=FakeDDB(), bucket="b", base_url="https://x/p", table="t", max_bytes=10)
    assert any("html" in e for e in ei.value.errors)


async def test_unpublish_deletes_object_then_clears_field():
    s3, ddb = FakeS3(), FakeDDB()
    s3.objects[page_key("m1", "abc")] = {}
    await unpublish_page("m1", s3=s3, ddb=ddb, bucket="b", table="t", page_id="abc")
    assert s3.deleted == [page_key("m1", "abc")]
    assert ddb.updates[0]["UpdateExpression"] == "REMOVE page"


async def test_unpublish_keeps_field_when_delete_fails():
    class FailingS3(FakeS3):
        async def delete_object(self, **kw):
            raise RuntimeError("boom")

    ddb = FakeDDB()
    with pytest.raises(RuntimeError):
        await unpublish_page("m1", s3=FailingS3(), ddb=ddb, bucket="b", table="t", page_id="abc")
    assert ddb.updates == []


def test_ddb_item_to_dict():
    item = {"meetingId": {"S": "m1"}, "messages": {"L": [{"M": {"speaker": {"S": "me"}, "n": {"N": "2"}}}]},
            "page": {"M": {"pageId": {"S": "abc"}}}}
    d = ddb_item_to_dict(item)
    assert d["meetingId"] == "m1" and d["messages"][0]["speaker"] == "me" and d["messages"][0]["n"] == 2
    assert d["page"]["pageId"] == "abc"
