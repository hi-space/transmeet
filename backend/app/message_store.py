"""회의 대화 저장소 — `transmeet-messages` 테이블 읽기·쓰기.

메시지는 회의 항목의 `messages` 리스트가 아니라 별도 테이블에 한 건씩 저장한다.
파티션 키 `meetingId`, 정렬 키 `sk` = `{timestamp}#{id}`.
`ddb`는 aiobotocore 저수준 클라이언트이므로 속성은 `{"S": ...}` 형식으로 직접 쓴다.
"""
import asyncio
import random
from typing import Dict, List, Optional

from botocore.exceptions import ClientError

from .config import config

# 필수 6개 필드 외에 선택 필드 targetLanguage('ko'|'en')가 있으면 함께 저장·반환한다.
OPTIONAL_FIELDS = ("targetLanguage",)
MESSAGE_FIELDS = ("id", "speaker", "originalText", "translatedText", "detectedLanguage", "timestamp")

# 같은 회의 항목에 트랜잭션이 겹치면(TransactionConflict) botocore 는 재시도하지 않는다.
# 취소된 트랜잭션은 아무것도 적용하지 않으므로 다시 보내도 messageCount 가 두 번 오르지 않는다.
MAX_RETRIES = 4  # 첫 시도 뒤 재시도 횟수
BASE_DELAY = 0.05  # 재시도 간격(초), 시도마다 두 배 + 지터
RETRYABLE_REASONS = {"TransactionConflict", "ThrottlingError"}
RETRYABLE_CODES = {"ThrottlingException", "ProvisionedThroughputExceededException"}

# 테스트에서 바꿔 끼울 수 있게 모듈 변수로 둔다
_sleep = asyncio.sleep


def sort_key(timestamp: str, message_id: str) -> str:
    """정렬 키. 같은 timestamp 의 메시지가 덮어쓰이지 않도록 id 를 붙인다."""
    return f"{timestamp}#{message_id}"


async def append_message(ddb, meeting_id: str, message: Dict[str, str], *, updated_at: str) -> None:
    """메시지 저장과 회의 항목의 messageCount·updatedAt 갱신을 한 트랜잭션으로 처리한다.

    회의 항목이 없으면 트랜잭션이 취소된다(is_missing_meeting 으로 판별, 재시도 안 함).
    트랜잭션 충돌·스로틀링이면 간격을 늘려 가며 최대 MAX_RETRIES 번 다시 보내고, 그래도 안 되면 던진다.
    """
    item = {field: {"S": message.get(field) or ""} for field in MESSAGE_FIELDS}
    for field in OPTIONAL_FIELDS:
        if message.get(field):
            item[field] = {"S": message[field]}
    item["meetingId"] = {"S": meeting_id}
    item["sk"] = {"S": sort_key(message["timestamp"], message["id"])}
    transact_items = [
        {"Put": {"TableName": config.MESSAGES_TABLE, "Item": item}},
        {
            "Update": {
                "TableName": config.MEETINGS_TABLE,
                "Key": {"meetingId": {"S": meeting_id}},
                "UpdateExpression": "SET #updatedAt = :ts ADD messageCount :one",
                "ConditionExpression": "attribute_exists(meetingId)",
                "ExpressionAttributeNames": {"#updatedAt": "updatedAt"},
                "ExpressionAttributeValues": {
                    ":ts": {"S": updated_at},
                    ":one": {"N": "1"},
                },
            }
        },
    ]
    for attempt in range(MAX_RETRIES + 1):
        try:
            await ddb.transact_write_items(TransactItems=transact_items)
            return
        except ClientError as err:
            if attempt == MAX_RETRIES or not is_retryable_cancellation(err):
                raise
        delay = BASE_DELAY * (2 ** attempt)
        await _sleep(delay + random.uniform(0, delay))


def is_retryable_cancellation(err: ClientError) -> bool:
    """다시 보내면 성공할 수 있는 실패인지 — 트랜잭션 충돌·스로틀링. 회의 없음(조건 실패)은 아니다."""
    error = err.response.get("Error", {})
    code = error.get("Code")
    if code in RETRYABLE_CODES:
        return True
    if code != "TransactionCanceledException":
        return False
    reasons = err.response.get("CancellationReasons") or []
    codes = {r.get("Code") for r in reasons}
    if "ConditionalCheckFailed" in codes:
        return False
    return bool(codes & RETRYABLE_REASONS)


def is_missing_meeting(err: ClientError) -> bool:
    """트랜잭션이 '회의 항목 없음'(조건 실패)으로 취소됐는지."""
    error = err.response.get("Error", {})
    if error.get("Code") != "TransactionCanceledException":
        return False
    reasons = err.response.get("CancellationReasons") or []
    return any(r.get("Code") == "ConditionalCheckFailed" for r in reasons)


async def load_messages(
    ddb,
    meeting_id: str,
    *,
    limit: Optional[int] = None,
    table: Optional[str] = None,
) -> List[Dict[str, str]]:
    """회의의 메시지를 정렬 키 오름차순으로 읽는다(강한 일관성, 페이지 끝까지).

    limit 이 있으면 앞에서부터 그 개수까지만 읽고, 채우면 더 요청하지 않는다.
    limit 이 0 이하면 읽지 않고 빈 목록을 돌려준다(DynamoDB 는 Limit<1 을 거부한다).
    """
    if limit is not None and limit <= 0:
        return []
    messages: List[Dict[str, str]] = []
    start_key = None
    while True:
        params = {
            "TableName": table or config.MESSAGES_TABLE,
            "KeyConditionExpression": "meetingId = :m",
            "ExpressionAttributeValues": {":m": {"S": meeting_id}},
            "ConsistentRead": True,
        }
        if limit is not None:
            params["Limit"] = limit - len(messages)
        if start_key:
            params["ExclusiveStartKey"] = start_key
        resp = await ddb.query(**params)
        for item in resp.get("Items", []):
            msg = {f: item.get(f, {}).get("S", "") for f in MESSAGE_FIELDS}
            for f in OPTIONAL_FIELDS:
                if item.get(f, {}).get("S"):
                    msg[f] = item[f]["S"]
            messages.append(msg)
            if limit is not None and len(messages) >= limit:
                return messages
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            return messages
