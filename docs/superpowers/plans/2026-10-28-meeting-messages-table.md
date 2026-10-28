# 회의 대화 별도 테이블 분리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 회의 대화를 `transmeet-meetings` 항목 안의 `messages` 리스트에서 새 테이블 `transmeet-messages`(메시지당 항목 하나)로 옮긴다. 기존 124개 회의도 빠짐없이 이전한다.

**Architecture:** 쓰기는 WS 백엔드(Python)에서만 일어난다. 메시지 Put과 회의 항목 카운터 Update를 트랜잭션 하나로 묶는다. 읽기는 Python(`backend/app/message_store.py`)과 TypeScript(`infra/lambda/shared/messages.ts`)의 도우미가 `meetingId`로 Query해서 지금과 같은 모양의 배열을 돌려준다. 이전은 멱등 스크립트로 복사 → 검증 → 조건부 원본 제거 순서로 한다.

**Tech Stack:** Python 3 · aiobotocore · pytest-asyncio / TypeScript Lambda · `@aws-sdk/lib-dynamodb` · `node --test`(트랜스파일 + 가짜 require) / AWS CDK / boto3(이전 스크립트)

**Spec:** `docs/superpowers/specs/2026-10-28-meeting-messages-table-design.md`

## Global Constraints

- 테이블 이름 `transmeet-messages`, 파티션 키 `meetingId`(S), 정렬 키 `sk`(S) = `{timestamp}#{id}`, 온디맨드, PITR 켜짐, `RemovalPolicy.RETAIN`
- 메시지 속성은 지금 맵 그대로: `id, speaker, originalText, translatedText, detectedLanguage, timestamp`(+ 키 `meetingId`, `sk`)
- 모든 메시지 읽기는 `ConsistentRead=True`, 정렬 키 오름차순, `LastEvaluatedKey`가 없어질 때까지 이어 받는다
- `GET /meetings/{id}` 응답의 `messages` 배열 모양은 바꾸지 않는다. 프런트(`src/`)는 수정하지 않는다
- 환경 변수 이름은 `MESSAGES_TABLE`. Lambda `commonEnv`, WS 컨테이너 환경, `backend/app/config.py` 세 곳에 둔다
- 같은 작업 트리를 다른 세션이 함께 쓴다. **배포는 main 에 병합한 커밋에서만 하고, 그 커밋이 현재 배포된 다른 세션 작업을 모두 포함하는지 사용자에게 확인받는다.** worktree 에서 `cdk deploy` 하면 다른 세션이 배포한 Lambda 코드를 되돌린다
- 백엔드 배포: 고유 태그로 `--platform linux/amd64` 이미지 푸시 → `cdk deploy -c wsImageTag=<태그>`
- 운영 데이터를 바꾸는 단계(복사·원본 제거)는 사용자 확인 후 실행한다. 스크립트 기본값은 드라이런이다
- 주석·로그 문구는 기존 파일처럼 한국어

## Review Focus

1. **트랜잭션 취소 사유 구분.** 회의가 지워진 상태에서 들어온 발언은 `TransactionCanceledException`과 `CancellationReasons[1].Code == "ConditionalCheckFailed"`로 온다. 이때는 지금처럼 경고만 남기고, 다른 사유는 예외 로그를 남겨야 한다 → Task 3 `test_persist_skips_deleted_meeting`
2. **1 MB 를 넘는 회의.** 메시지 1,000개 이상이면 Query 가 여러 페이지로 나뉜다. 첫 페이지만 읽으면 요약·페이지에서 대화가 잘린다 → Task 2 `test_load_messages_follows_pages`, Task 5 `loadMessages follows LastEvaluatedKey`
3. **같은 시각의 메시지.** 한 번에 넘긴 구간은 `timestamp` 가 같다. 정렬 키에 id 가 빠지면 서로 덮어써 사라진다 → Task 2 `test_same_timestamp_messages_do_not_collide`
4. **이전 중 옛 코드의 쓰기.** 검증과 원본 제거 사이에 붙은 발언이 조용히 지워지면 안 된다 → Task 7 `test_remove_skips_when_list_grew`
5. **삭제 중간 실패.** 메시지 BatchWrite 의 `UnprocessedItems` 를 버리면 주인 없는 메시지가 남는다 → Task 5 `deleteMessages retries unprocessed items`

---

### Task 0: 준비 — 브랜치 최신화와 기준 테스트

**Files:** 없음(작업 환경)

- [x] **Step 1:** (2026-10-28 완료) 사용자 결정: main 작업 트리의 미커밋 상태를 기준으로 시작한다. 그 상태를 커밋 `d06f38b`(기준 스냅샷)로 옮겼고, 원본과 파일 해시가 같음을 확인했다. 병합 때 main 에 따로 커밋된 같은 변경과 맞춰야 한다(Task 8 Step 1)
- [ ] **Step 2:** worktree 에 의존성을 설치한다. `npm ci`(루트), `npm ci --prefix infra`, `python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements-dev.txt`
- [ ] **Step 3:** 기준 테스트를 돌린다
  Run: `cd backend && .venv/bin/pytest -q` 그리고 루트에서 `npm run test:ui && node --test tests/meetingTitle.test.mjs`
  Expected: 모두 PASS. 실패가 있으면 이 작업 전부터 있던 것으로 기록하고 사용자에게 알린다

### Task 1: 인프라 — 메시지 테이블, 권한, 환경 변수

**Files:**
- Modify: `infra/lib/transmeet-stack.ts`(회의 테이블 정의 아래, `grantReadWriteData` 두 곳, `commonEnv`, WS 컨테이너 `environment`)
- Modify: `backend/app/config.py`

**Interfaces:**
- Produces: CDK 상수 `MESSAGES_TABLE = 'transmeet-messages'`, 환경 변수 `MESSAGES_TABLE`, `config.MESSAGES_TABLE: str = "transmeet-messages"`

- [ ] **Step 1:** 스택에 `MessagesTable`을 추가한다(Global Constraints 의 키·설정). `lambdaRole`과 `wsTaskRole`에 `grantReadWriteData`를 주고, `commonEnv`와 WS 컨테이너 환경에 `MESSAGES_TABLE`을 넣는다. 페이지 에이전트 Runtime 역할에는 주지 않는다(Runtime 은 payload 로 메시지를 받는다)
- [ ] **Step 2:** `config.py`에 `MESSAGES_TABLE`을 추가한다. `PAGES_TABLE` 위 주석 중 "미팅 항목은 발언 전체를 인라인으로 담아"는 이제 사실이 아니므로 고친다
- [ ] **Step 3:** 합성 결과를 확인한다
  Run: `cd infra && npx cdk synth TransmeetStack -q > /tmp/synth.yaml && command grep -c "transmeet-messages" /tmp/synth.yaml`
  Expected: 1 이상. 같은 파일에서 `MESSAGES_TABLE`이 Lambda 와 컨테이너 환경 모두에 보인다
- [ ] **Step 4: Commit** `feat(infra): 회의 대화 테이블 transmeet-messages 추가`

### Task 2: Python 메시지 저장소

**Files:**
- Create: `backend/app/message_store.py`
- Test: `backend/tests/test_message_store.py`

**Interfaces:**
- Consumes: `config.MESSAGES_TABLE`, `config.MEETINGS_TABLE`
- Produces:
  - `sort_key(timestamp: str, message_id: str) -> str` → `f"{timestamp}#{message_id}"`
  - `async append_message(ddb, meeting_id: str, message: Dict[str, str], *, updated_at: str) -> None`. `message`는 6개 필드를 가진 dict. 트랜잭션: 메시지 테이블 `Put` + 회의 테이블 `Update`(`SET #updatedAt = :ts ADD messageCount :one`, 조건 `attribute_exists(meetingId)`). 예외는 호출자에게 그대로 던진다
  - `is_missing_meeting(err: ClientError) -> bool`. `TransactionCanceledException`이고 `CancellationReasons` 중 하나라도 `ConditionalCheckFailed`이면 True
  - `async load_messages(ddb, meeting_id: str, *, limit: Optional[int] = None) -> List[Dict[str, str]]`. 6개 필드의 평범한 dict 목록, 정렬 키 순서. `limit`이면 앞에서부터 그 개수까지만

- [ ] **Step 1: 실패하는 테스트를 쓴다.** 가짜 DDB(`transact_write_items`, `query` 기록. `query`는 미리 넣은 페이지 목록을 `ExclusiveStartKey` 순서로 돌려줌)로 다음을 검증한다:
  - `test_append_message_writes_put_and_counter`: `TransactItems[0]["Put"]["Item"]["sk"]["S"] == "2026-10-28T00:00:01.000000+00:00#m-1"`, `[1]["Update"]["ConditionExpression"] == "attribute_exists(meetingId)"`, `"ADD messageCount :one"`이 UpdateExpression 에 들어 있음
  - `test_same_timestamp_messages_do_not_collide`: 같은 `timestamp`에 id 만 다른 두 메시지의 `sk`가 다름
  - `test_is_missing_meeting`: `CancellationReasons=[{"Code": "None"}, {"Code": "ConditionalCheckFailed"}]`이면 True, `[{"Code": "ThrottlingError"}, {"Code": "None"}]`이면 False, 다른 에러 코드면 False
  - `test_load_messages_follows_pages`: 페이지 2개(각 2개 메시지)를 모두 이어 받아 4개를 순서대로 돌려주고, 모든 호출이 `ConsistentRead=True`·`ScanIndexForward` 생략 또는 True
  - `test_load_messages_limit`: `limit=3`이면 3개만 돌려주고, 3개를 채운 뒤에는 다음 페이지를 요청하지 않음
- [ ] **Step 2:** `cd backend && .venv/bin/pytest tests/test_message_store.py -q` → FAIL(모듈 없음)
- [ ] **Step 3:** `message_store.py`를 구현한다. DynamoDB 타입 변환은 저수준 클라이언트 형식(`{"S": ...}`)으로 직접 쓴다(ws_handler 와 같은 방식)
- [ ] **Step 4:** 같은 명령 → PASS
- [ ] **Step 5: Commit** `feat(backend): 회의 대화 저장소 모듈 추가`

### Task 3: WS 백엔드 쓰기 전환

**Files:**
- Modify: `backend/app/ws_handler.py`(`_process_segment` 저장 블록 약 508–556행, TTS 저장 블록 약 1076–1112행)
- Test: `backend/tests/test_message_persist.py`

**Interfaces:**
- Consumes: Task 2 `append_message`, `is_missing_meeting`
- Produces: `async _persist_message(meeting_id: str, message: Dict[str, str], *, log_tag: str) -> None`(ws_handler 내부). 예외를 밖으로 던지지 않는다. 회의가 없으면 `"[{log_tag}] Skipped save — meeting does not exist: meeting_id=%s"` 경고, 그 밖에는 `"[{log_tag}] DynamoDB save failed for message_id=%s meeting_id=%s"` 예외 로그

- [ ] **Step 1: 실패하는 테스트를 쓴다.** `ws_handler.dynamodb_client`를 가짜로 바꿔서 검증한다:
  - `test_persist_writes_transaction`: `transact_write_items`가 한 번 불림
  - `test_persist_skips_deleted_meeting`: 조건 실패로 취소되면 예외 없이 WARNING 로그에 `"meeting does not exist"`
  - `test_persist_logs_other_errors`: `ThrottlingException`이면 예외 없이 ERROR 로그
  - `test_no_list_append_left`: `"list_append" not in Path(ws_handler.__file__).read_text()`
- [ ] **Step 2:** `.venv/bin/pytest tests/test_message_persist.py -q` → FAIL
- [ ] **Step 3:** `_persist_message`를 추가하고 두 저장 블록을 이것으로 바꾼다. `log_tag`는 각각 `"process_segment"`, `"tts_request"`. 메시지 dict 의 필드 값은 지금 각 블록에 있는 것 그대로다(TTS 는 `speaker="me"`, `originalText=translated_text`, `translatedText=text`, `detectedLanguage="ko"`)
- [ ] **Step 4:** `.venv/bin/pytest -q` → 전체 PASS
- [ ] **Step 5: Commit** `refactor(ws): 발언 저장을 메시지 테이블 트랜잭션으로 전환`

### Task 4: WS 백엔드 읽기 전환(요약·페이지)

**Files:**
- Modify: `backend/app/ws_handler.py`(`_stream_summary`의 저장 메시지 경로 약 1217–1230행)
- Modify: `backend/app/page_agent.py`(`_load_meeting` 약 309–314행. `PageAgentDeps`에 `messages_table: str` 추가, `from_config`에서 `config.MESSAGES_TABLE`)
- Test: `backend/tests/test_stream_summary.py`, `backend/tests/test_page_agent_run.py`, `backend/tests/test_meeting_end.py`

**Interfaces:**
- Consumes: Task 2 `load_messages`
- Produces: `_load_meeting`이 회의 dict 에 `"messages": load_messages(...)` 결과를 붙여 돌려준다. 그래서 `build_meeting_payload`와 `prefer_stored_messages` 분기는 바꾸지 않는다

- [ ] **Step 1: 테스트 가짜 데이터를 옮긴다.** 세 테스트 파일의 `FakeDDB`에서 회의 항목의 `"messages"`를 빼고, `query(TableName=MESSAGES_TABLE, ...)`가 그 메시지를 돌려주게 한다. 테스트 하나를 추가한다: `test_page_agent_run.py::test_stored_messages_come_from_messages_table`. 회의 항목에 `messages`가 남아 있어도(옛 데이터 흉내) payload 는 메시지 테이블 내용을 쓴다
- [ ] **Step 2:** `.venv/bin/pytest -q` → 새 테스트와 옮긴 테스트가 FAIL
- [ ] **Step 3:** 두 읽기 경로를 `load_messages`로 바꾼다. `_stream_summary`의 회의 `get_item`에는 `ProjectionExpression="title, summaryStyle"`을 붙인다(인라인 경로와 같게)
- [ ] **Step 4:** `.venv/bin/pytest -q` → 전체 PASS
- [ ] **Step 5: Commit** `refactor(ws): 요약·페이지가 메시지 테이블에서 대화를 읽도록`

### Task 5: TypeScript 메시지 도우미

**Files:**
- Create: `infra/lambda/shared/messages.ts`
- Test: `tests/meetingMessages.test.mjs`(`tests/meetingTitle.test.mjs`의 트랜스파일 + 가짜 require 방식)

**Interfaces:**
- Produces:
  - `export interface StoredMessage { id: string; speaker: string; originalText: string; translatedText: string; detectedLanguage: string; timestamp: string }`
  - `export async function loadMessages(ddb: DynamoDBDocumentClient, meetingId: string, opts?: { limit?: number }): Promise<StoredMessage[]>`. `process.env.MESSAGES_TABLE`, `QueryCommand`, `ConsistentRead: true`, 반환에서 `meetingId`·`sk` 제거
  - `export async function deleteMessages(ddb: DynamoDBDocumentClient, meetingId: string): Promise<void>`. 키만 Query(`ProjectionExpression: 'meetingId, sk'`) → `BatchWriteCommand` 25개씩. `UnprocessedItems`는 50·100·200 ms 간격으로 최대 3번 다시 보내고, 그래도 남으면 `throw new Error('Could not delete meeting messages')`

- [ ] **Step 1: 실패하는 테스트를 쓴다:**
  - `loadMessages follows LastEvaluatedKey`: 두 페이지를 이어 받아 순서대로, 결과에 `sk`가 없음
  - `loadMessages honours limit`: `limit: 20`이면 20개, 다음 페이지는 요청하지 않음
  - `deleteMessages batches by 25`: 메시지 60개 → BatchWrite 3번(25·25·10)
  - `deleteMessages retries unprocessed items`: 첫 응답의 `UnprocessedItems` 2개가 다음 요청에 다시 들어감. 4번 연속 남으면 reject
- [ ] **Step 2:** `node --test tests/meetingMessages.test.mjs` → FAIL
- [ ] **Step 3:** 구현한다
- [ ] **Step 4:** 같은 명령 → PASS
- [ ] **Step 5: Commit** `feat(lambda): 회의 대화 읽기·삭제 공용 도우미`

### Task 6: Lambda 읽기·생성·삭제 전환

**Files:**
- Modify: `infra/lambda/meetings/index.ts`(GET `/meetings/{id}`, POST `/title`, POST `/meetings`, DELETE)
- Modify: `infra/lambda/summarize/index.ts`(약 196행 `result.Item.messages`)
- Test: `tests/meetingTitle.test.mjs`, `tests/meetingBrief.test.mjs`, 새 `tests/meetingsApi.test.mjs`

**Interfaces:**
- Consumes: Task 5 `loadMessages`, `deleteMessages`(import 경로 `'../shared/messages'`)

- [ ] **Step 1: 테스트를 고치고 추가한다.** 기존 두 테스트의 가짜 require 에 `'../shared/messages'`(트랜스파일한 실제 모듈)와 `QueryCommand`·`BatchWriteCommand`를 넣는다. `item.messages = [...]` 대신 메시지 테이블 Query 응답으로 준다. 새 `meetingsApi.test.mjs`:
  - `GET /meetings/{id} attaches messages from the messages table`: 응답 `messages`가 Query 결과와 같고, 회의 항목에 남은 옛 `messages`는 무시
  - `POST /meetings stores no messages attribute`: Put 한 Item 에 `messages` 없음, 응답 본문에는 `messages: []`, `messageCount: 0`
  - `DELETE deletes messages before the meeting item`: 보낸 명령 순서에서 마지막 `BatchWriteCommand`가 회의 테이블 `DeleteCommand`보다 앞
  - `DELETE keeps the meeting when message deletion fails`: `deleteMessages`가 reject 하면 회의 `DeleteCommand`를 보내지 않고 500
- [ ] **Step 2:** `node --test tests/meetingsApi.test.mjs tests/meetingTitle.test.mjs tests/meetingBrief.test.mjs` → FAIL
- [ ] **Step 3:** 구현한다. 상세 조회는 회의 항목의 `messages` 키를 지우고 `loadMessages` 결과를 붙인다. 제목은 `loadMessages(ddb, id, { limit: 20 })`. 요약 Lambda 는 `loadMessages`. 생성은 `messages: []`를 Item 에서 빼고 응답에만 붙인다(`messages`를 투사하지 않는다는 기존 주석도 고친다)
- [ ] **Step 4:** 같은 명령에 `npm run test:ui`까지 → 전체 PASS. `cd infra && npx tsc --noEmit -p . && npx cdk synth -q` 성공(Lambda 번들 확인)
- [ ] **Step 5: Commit** `refactor(lambda): 회의 상세·제목·요약·삭제를 메시지 테이블로 전환`

### Task 7: 이전 스크립트

**Files:**
- Create: `backend/scripts/migrate_messages.py`(boto3, 동기)
- Test: `backend/tests/test_migrate_messages.py`

**Interfaces:**
- Consumes: Task 2 `sort_key`(같은 키 규칙)
- Produces: CLI `python -m scripts.migrate_messages {copy,verify,remove} [--execute] [--meeting-id ID]`. 기본은 드라이런이고 할 일만 출력한다. 함수:
  - `copy_meeting(ddb, meeting: dict) -> int`: 원본 `messages`를 BatchWrite(Put, 25개씩, 미처리분 재시도)하고 복사한 개수를 돌려준다
  - `verify_meeting(ddb, meeting: dict) -> tuple[bool, int, int]`: (일치 여부, 원본 개수, 테이블 개수). 일치 = id 집합이 같고 개수가 같음
  - `remove_meeting(ddb, meeting_id: str, verified_count: int) -> bool`: `REMOVE messages SET messageCount = :n`, 조건 `size(messages) = :n`. 조건 실패면 False

- [ ] **Step 1: 실패하는 테스트를 쓴다**(가짜 boto3 클라이언트):
  - `test_copy_is_idempotent`: 두 번 돌려도 테이블의 항목 수가 원본 개수와 같음
  - `test_verify_detects_missing`: 테이블에 하나가 빠지면 `(False, 3, 2)`
  - `test_remove_skips_when_list_grew`: 가짜가 `size(messages)` 조건을 원본 길이와 비교해 실패를 내면 `remove_meeting`이 False 를 돌려주고 원본이 남음
  - `test_remove_sets_message_count`: 성공하면 UpdateExpression 에 `SET messageCount = :n`, `:n == verified_count`
  - `test_dry_run_writes_nothing`: `--execute` 없이 `copy`를 돌리면 쓰기 호출 0회
- [ ] **Step 2:** `.venv/bin/pytest tests/test_migrate_messages.py -q` → FAIL
- [ ] **Step 3:** 구현한다. 회의 스캔은 `ProjectionExpression="meetingId, messages"`로 페이지를 끝까지 이어 받는다. `remove`는 회의마다 `verify`를 다시 한 뒤에만 지운다. 끝에 요약을 출력한다: 처리·일치·불일치·조건 실패 회의 수, 불일치 id 목록
- [ ] **Step 4:** 같은 명령 → PASS. `.venv/bin/pytest -q` 전체 PASS
- [ ] **Step 5: Commit** `feat(scripts): 회의 대화 이전 스크립트(copy·verify·remove)`

### Task 8: 배포와 이전 (운영, 사용자 확인 단계 포함)

**Files:** 없음(운영 작업). 결과는 사용자에게 보고한다

- [ ] **Step 1: 병합 확인.** 이 단계에서는 브랜치 전체를 병합하지 않는다. 배포 1 은 "지금 main + `72d6ef0`(Task 1, 테이블 추가)만 cherry-pick" 한 상태에서 한다. 브랜치 전체는 배포 2(Step 4) 바로 전에 병합한다 — 그 전에 병합하면 옛 코드가 도는 동안 새 코드가 섞여 배포될 수 있다. 병합 전 사용자에게 main 이 다른 세션의 배포된 작업을 모두 담고 있는지, 병합해도 되는지 확인한다
- [ ] **Step 2: 배포 1 — 테이블만.** 먼저 지금 돌고 있는 WS 이미지 태그를 찾는다: `aws ecs describe-services` 로 서비스의 `taskDefinition` 을 얻고 `aws ecs describe-task-definition` 의 `containerDefinitions[].image` 에서 태그를 읽는다. 그다음 main + `72d6ef0` cherry-pick 상태에서 `cd infra && npx cdk deploy TransmeetStack -c wsImageTag=<지금 배포된 태그>`. **`-c wsImageTag` 를 절대 빼지 않는다** — 기본값이 `latest` 라 빼면 의도하지 않은 이미지로 바뀔 수 있다. 확인: `aws dynamodb describe-table --table-name transmeet-messages` → `TableStatus: ACTIVE`, `describe-continuous-backups` → PITR `ENABLED`, ECS 태스크 정의 이미지 태그가 배포 전과 같음
- [ ] **Step 3: 복사(1차).** `python -m scripts.migrate_messages copy`(드라이런)으로 회의 수·메시지 수가 125·33,008 근처인지 보고 → 사용자 확인 → `--execute`
- [ ] **Step 4: 배포 2 — 코드 전환.** 이때 브랜치 전체를 main 에 병합한다(Step 1 의 사용자 확인 뒤). 녹음 중인 회의가 없는지 사용자에게 확인한다. 고유 태그로 백엔드 이미지를 푸시하고 `cdk deploy -c wsImageTag=<태그>`. `aws ecs describe-services` → `deployments` 1개, `runningCount == desiredCount`, 태스크 정의가 새 리비전
- [ ] **Step 5: 복사(2차) + 검증 + 고아 확인.** `copy --execute` 다시, 그다음 `verify` → 불일치 0. 불일치가 있으면 멈추고 보고한다. 이어서 `orphans`(드라이런)로 회의 항목 없이 남은 메시지(1차 복사 뒤 옛 Lambda 로 지운 회의)를 회의별 개수로 본다 → 사용자 확인 → `orphans --execute`
- [ ] **Step 6: 실제 확인.** 앱에서 긴 회의(메시지 1,156개) 하나를 열어 대화가 끝까지 보이는지, 새 회의에서 두세 마디 녹음 후 다시 열었을 때 보이는지 확인한다
- [ ] **Step 7: 원본 제거.** `remove`(드라이런) → 사용자 확인 → `remove --execute`. 조건 실패 회의가 있으면 그 회의만 `copy`→`verify`→`remove`를 다시 한다. 끝나면 `orphans`(드라이런)를 한 번 더 돌려 삭제·저장 경합으로 남은 고아가 있는지 보고, 있으면 사용자 확인 뒤 `orphans --execute`
  - **되돌리기 한계:** 옛 코드로의 롤백은 이 단계(원본 제거) 전까지만 안전하다. 제거 뒤에는 회의 항목에 `messages` 가 없으므로 옛 코드로 돌아가면 모든 회의의 대화가 빈 채로 보인다
- [ ] **Step 8: 효과 측정과 보고.** `GET /meetings` Lambda 실행 시간(p50·p95)과 BatchGet RCU 를 전환 전 값(1.3–1.85초, 약 1,800 RCU)과 비교해 보고한다. 목록용 GSI(방법 A)가 아직 필요한지 판단할 근거로 쓴다
