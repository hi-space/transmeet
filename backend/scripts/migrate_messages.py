#!/usr/bin/env python3
"""회의 대화 이전 — transmeet-meetings 의 messages 리스트를 transmeet-messages 테이블로 옮긴다.

  copy    원본 messages 를 메시지 테이블에 BatchWrite(덮어쓰기라 여러 번 돌려도 같다)
  verify  원본 messages 의 id 가 모두 메시지 테이블에 있는지 확인(읽기 전용).
          전환 뒤 새 발언은 테이블에만 들어가므로 테이블에 id 가 더 있어도 일치로 본다.
          messageCount 가 테이블 개수와 다르면 경고만 한다(막지 않음)
  remove  회의마다 다시 검증하고, 일치한 회의만 원본 messages 를 지운다(조건 size(messages) = 검증한 개수).
          messageCount 는 앱이 트랜잭션 ADD 로 올리므로 건드리지 않는다

기본은 드라이런이다(할 일만 출력, 쓰기 0회). 실제로 쓰려면 --execute 를 붙인다.
backend/ 에서 실행: python -m scripts.migrate_messages {copy,verify,remove} [--execute] [--meeting-id ID]
"""
import argparse
import sys
import time
from typing import Callable, Dict, Iterator, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

from app.config import config
from app.message_store import sort_key

BATCH_SIZE = 25  # BatchWriteItem 한 번에 넣을 수 있는 최대 개수
MAX_ATTEMPTS = 8  # 미처리분 재시도 횟수(첫 시도 포함)
BASE_DELAY = 0.2  # 재시도 간격(초), 시도마다 두 배

# 회의 스캔·GetItem 프로젝션
PROJECTION = "meetingId, messages, messageCount"

# 테스트에서 바꿔 끼울 수 있게 모듈 변수로 둔다
_sleep = time.sleep


def _meeting_id(meeting: Dict) -> str:
    return meeting["meetingId"]["S"]


def _original_messages(meeting: Dict) -> List[Dict]:
    """회의 항목의 messages(L of M)를 맵 목록으로. 속성이 없으면 빈 목록."""
    return [entry["M"] for entry in meeting.get("messages", {}).get("L", [])]


def _to_item(meeting_id: str, message: Dict) -> Dict:
    """원본 맵의 속성을 그대로 두고 키(meetingId, sk)만 더한다."""
    try:
        timestamp = message["timestamp"]["S"]
        message_id = message["id"]["S"]
    except KeyError as exc:
        raise ValueError(f"{meeting_id}: id/timestamp 없는 메시지 {message!r}") from exc
    return {**message, "meetingId": {"S": meeting_id}, "sk": {"S": sort_key(timestamp, message_id)}}


def _batch_put(ddb, requests: List[Dict]) -> None:
    """25개 이하 PutRequest 를 쓰고, 미처리분은 간격을 늘려 가며 다시 보낸다. 끝내 남으면 예외."""
    pending = requests
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            _sleep(BASE_DELAY * (2 ** (attempt - 1)))
        resp = ddb.batch_write_item(RequestItems={config.MESSAGES_TABLE: pending})
        pending = resp.get("UnprocessedItems", {}).get(config.MESSAGES_TABLE, [])
        if not pending:
            return
    raise RuntimeError(f"BatchWriteItem 미처리 {len(pending)}건이 {MAX_ATTEMPTS}번 시도 뒤에도 남음")


def copy_meeting(ddb, meeting: Dict) -> int:
    """원본 messages 를 메시지 테이블에 Put 한다(25개씩). 복사한 개수를 돌려준다."""
    meeting_id = _meeting_id(meeting)
    items = [_to_item(meeting_id, m) for m in _original_messages(meeting)]
    for start in range(0, len(items), BATCH_SIZE):
        chunk = items[start:start + BATCH_SIZE]
        _batch_put(ddb, [{"PutRequest": {"Item": item}} for item in chunk])
    return len(items)


def _table_ids(ddb, meeting_id: str) -> List[str]:
    """메시지 테이블에서 회의의 id 를 전부 읽는다(강한 일관성, 페이지 끝까지)."""
    ids: List[str] = []
    start_key = None
    while True:
        params = {
            "TableName": config.MESSAGES_TABLE,
            "KeyConditionExpression": "meetingId = :m",
            "ExpressionAttributeValues": {":m": {"S": meeting_id}},
            "ProjectionExpression": "#id",
            "ExpressionAttributeNames": {"#id": "id"},
            "ConsistentRead": True,
        }
        if start_key:
            params["ExclusiveStartKey"] = start_key
        resp = ddb.query(**params)
        ids.extend(item.get("id", {}).get("S", "") for item in resp.get("Items", []))
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            return ids


def verify_meeting(ddb, meeting: Dict) -> Tuple[bool, int, int]:
    """(일치 여부, 원본 개수, 테이블 개수). 일치 = 원본의 모든 id 가 테이블에 있음.

    전환 뒤 앱은 새 발언을 테이블에만 쓰므로 테이블에 id 가 더 있는 것은 정상이다.
    """
    original = [m.get("id", {}).get("S", "") for m in _original_messages(meeting)]
    table = _table_ids(ddb, _meeting_id(meeting))
    matched = set(original) <= set(table)
    return matched, len(original), len(table)


def _message_count(meeting: Dict) -> Optional[int]:
    """회의 항목의 messageCount. 없으면 None."""
    value = meeting.get("messageCount", {}).get("N")
    return int(value) if value is not None else None


def remove_meeting(ddb, meeting_id: str, verified_count: int) -> bool:
    """원본 messages 를 지운다. messageCount 는 앱이 트랜잭션 ADD 로 올리므로 덮어쓰지 않는다.

    그사이 리스트가 늘었으면(조건 size(messages) = :n 실패) 지우지 않고 False.
    """
    try:
        ddb.update_item(
            TableName=config.MEETINGS_TABLE,
            Key={"meetingId": {"S": meeting_id}},
            UpdateExpression="REMOVE messages",
            ConditionExpression="size(messages) = :n",
            ExpressionAttributeValues={":n": {"N": str(verified_count)}},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise
    return True


def iter_meetings(ddb, meeting_id: Optional[str] = None) -> Iterator[Dict]:
    """회의 항목(meetingId, messages, messageCount)을 하나씩. meeting_id 가 있으면 그 회의만 GetItem."""
    if meeting_id:
        resp = ddb.get_item(
            TableName=config.MEETINGS_TABLE,
            Key={"meetingId": {"S": meeting_id}},
            ProjectionExpression=PROJECTION,
            ConsistentRead=True,
        )
        if "Item" not in resp:
            raise SystemExit(f"회의 {meeting_id} 없음")
        yield resp["Item"]
        return
    start_key = None
    while True:
        params = {
            "TableName": config.MEETINGS_TABLE,
            "ProjectionExpression": PROJECTION,
            "ConsistentRead": True,
        }
        if start_key:
            params["ExclusiveStartKey"] = start_key
        resp = ddb.scan(**params)
        yield from resp.get("Items", [])
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            return


def run(
    ddb,
    command: str,
    *,
    execute: bool = False,
    meeting_id: Optional[str] = None,
    out: Callable[..., None] = print,
) -> Dict:
    """명령 하나를 모든 회의(또는 한 회의)에 적용하고 요약을 돌려준다."""
    mode = "실행" if execute else "드라이런"
    summary = {
        "command": command,
        "execute": execute,
        "processed": 0,
        "messages": 0,
        "matched": 0,
        "mismatched_ids": [],
        "condition_failed_ids": [],
        "no_messages_ids": [],
        "message_count_mismatch_ids": [],
        "removed": 0,
    }
    for meeting in iter_meetings(ddb, meeting_id):
        mid = _meeting_id(meeting)
        has_list = "messages" in meeting
        count = len(_original_messages(meeting))
        summary["processed"] += 1
        summary["messages"] += count
        if not has_list:
            summary["no_messages_ids"].append(mid)

        if command == "copy":
            if not has_list:
                out(f"[{mode}] {mid}: messages 없음 — 복사할 것 없음")
            elif execute:
                copied = copy_meeting(ddb, meeting)
                out(f"[{mode}] {mid}: {copied}건 복사")
            else:
                out(f"[{mode}] {mid}: {count}건 복사 예정")
            continue

        # verify·remove 는 모두 검증부터(읽기만)
        ok, n_orig, n_table = verify_meeting(ddb, meeting)
        if ok:
            summary["matched"] += 1
        else:
            summary["mismatched_ids"].append(mid)
        status = "일치" if ok else "불일치(원본 id 가 테이블에 없음)"
        out(f"[{mode}] {mid}: {status} (원본 {n_orig}, 테이블 {n_table})")
        stored = _message_count(meeting)
        if stored != n_table:
            # 경고만: remove 를 막지 않고 종료 코드도 바꾸지 않는다
            summary["message_count_mismatch_ids"].append(mid)
            shown = "없음" if stored is None else stored
            out(f"[{mode}] {mid}: 경고 — messageCount 불일치 (messageCount {shown}, 테이블 {n_table})")

        if command != "remove":
            continue
        if not has_list:
            out(f"[{mode}] {mid}: messages 없음 — 지울 것 없음")
        elif not ok:
            out(f"[{mode}] {mid}: 검증 실패 — 건너뜀(원본 유지)")
        elif not execute:
            out(f"[{mode}] {mid}: 원본 {n_orig}건 제거 예정")
        elif remove_meeting(ddb, mid, n_orig):
            summary["removed"] += 1
            out(f"[{mode}] {mid}: 원본 {n_orig}건 제거")
        else:
            summary["condition_failed_ids"].append(mid)
            out(f"[{mode}] {mid}: 리스트가 늘어남 — copy/verify/remove 를 다시 돌릴 것")

    out(
        f"\n요약({command}, {mode}): 처리 {summary['processed']}개 회의 · 메시지 {summary['messages']}건"
        f" · 일치 {summary['matched']} · 불일치 {len(summary['mismatched_ids'])}"
        f" · 조건 실패 {len(summary['condition_failed_ids'])} · 제거 {summary['removed']}"
        f" · messages 없음 {len(summary['no_messages_ids'])}"
        f" · messageCount 불일치(경고) {len(summary['message_count_mismatch_ids'])}"
    )
    if summary["mismatched_ids"]:
        out("불일치 회의: " + ", ".join(summary["mismatched_ids"]))
    if summary["condition_failed_ids"]:
        out("조건 실패(리스트가 늘어남 — copy/verify/remove 다시): " + ", ".join(summary["condition_failed_ids"]))
    if summary["no_messages_ids"]:
        out("messages 없는 회의: " + ", ".join(summary["no_messages_ids"]))
    if summary["message_count_mismatch_ids"]:
        out("경고 — messageCount 불일치 회의: " + ", ".join(summary["message_count_mismatch_ids"]))
    if not execute and command != "verify":
        out("드라이런이라 아무것도 쓰지 않았다. 실제로 하려면 --execute 를 붙인다.")
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["copy", "verify", "remove"])
    parser.add_argument("--execute", action="store_true", help="실제로 쓴다(없으면 드라이런)")
    parser.add_argument("--meeting-id", help="이 회의 하나만 처리")
    args = parser.parse_args(argv)
    ddb = boto3.client("dynamodb", region_name=config.REGION)
    summary = run(ddb, args.command, execute=args.execute, meeting_id=args.meeting_id)
    # 불일치·조건 실패가 있으면 0 이 아닌 코드로 끝내 다음 단계로 넘어가지 않게 한다
    return 1 if summary["mismatched_ids"] or summary["condition_failed_ids"] else 0


if __name__ == "__main__":
    sys.exit(main())
