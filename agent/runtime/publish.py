"""Publish a validated page: S3 upload + DynamoDB `meeting.page` record.

Ported from the backend's publish_page. Synchronous boto3 — the graph calls it through
`asyncio.to_thread`. Fails closed: if the DynamoDB write fails the uploaded object is
removed again (best effort) so no orphan page stays reachable.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from .validator import validate_html

logger = logging.getLogger(__name__)

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


class PublishError(Exception):
    def __init__(self, errors: List[str], *, transient: bool = False):
        super().__init__("; ".join(errors))
        self.errors = errors
        self.transient = transient


@dataclass
class PublishedPage:
    page_id: str
    url: str
    generated_at: str
    title: str
    share_summary: str

    def to_item(self) -> Dict[str, Any]:
        return {
            "pageId": self.page_id,
            "url": self.url,
            "generatedAt": self.generated_at,
            "title": self.title,
            "shareSummary": self.share_summary,
        }


def page_key(meeting_id: str, page_id: str) -> str:
    return f"p/{meeting_id}/{page_id}.html"


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def ddb_item_to_dict(item: Dict[str, Any]) -> Dict[str, Any]:
    return {k: _plain(_deserializer.deserialize(v)) for k, v in (item or {}).items()}


def publish_html(
    meeting_id: str,
    html: str,
    title: str,
    share_summary: str,
    *,
    s3,
    ddb,
    bucket: str,
    base_url: str,
    table: str,
    max_bytes: int,
) -> PublishedPage:
    errors: List[str] = []
    title = (title or "").strip()
    if not isinstance(html, str) or not html.strip():
        errors.append("html is required and must be a non-empty string")
    if not title:
        errors.append("title is required")
    if errors:
        raise PublishError(errors)
    errors = validate_html(html, max_bytes)
    if errors:
        raise PublishError(errors)

    page_id = secrets.token_urlsafe(16)
    key = page_key(meeting_id, page_id)
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=html.encode("utf-8"),
        ContentType="text/html; charset=utf-8",
        CacheControl="no-cache",
    )
    page = PublishedPage(
        page_id=page_id,
        url=f"{base_url.rstrip('/')}/{meeting_id}/{page_id}.html",
        generated_at=datetime.now(timezone.utc).isoformat(),
        title=title,
        share_summary=(share_summary or "").strip(),
    )
    try:
        res = ddb.update_item(
            TableName=table,
            Key={"meetingId": {"S": meeting_id}},
            UpdateExpression="SET page = :p, updatedAt = :u",
            ConditionExpression="attribute_exists(meetingId)",
            ExpressionAttributeValues={
                ":p": _serializer.serialize(page.to_item()),
                ":u": {"S": page.generated_at},
            },
            ReturnValues="ALL_OLD",
        )
    except Exception as e:
        try:
            s3.delete_object(Bucket=bucket, Key=key)
        except Exception:
            logger.warning("[publish] failed to roll back upload %s", key, exc_info=True)
        code = (getattr(e, "response", None) or {}).get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            raise PublishError(["meeting not found"]) from e
        raise
    old_page = ddb_item_to_dict((res or {}).get("Attributes") or {}).get("page") or {}
    previous_page_id = old_page.get("pageId") if isinstance(old_page, dict) else None
    if previous_page_id and previous_page_id != page_id:
        try:
            s3.delete_object(Bucket=bucket, Key=page_key(meeting_id, previous_page_id))
        except Exception:
            logger.warning("[publish] failed to delete previous page %s/%s", meeting_id, previous_page_id, exc_info=True)
    return page
