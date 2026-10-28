# 내 양식(커스텀 요약 프롬프트) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 이름을 붙인 요약 양식(지시문 또는 예시 회의록)을 서버에 저장하고, 회의별로 골라 수동·자동·종료 요약에 쓰게 한다.

**Architecture:**
- 양식 목록은 새 DynamoDB 테이블과 새 `SummaryTemplatesFn`(`/me/summary-templates`)에 둔다.
- 회의에서 양식을 고르면 meetings Lambda가 양식을 서버에서 읽어 회의 레코드의 `summaryCustom`에 사본을 저장한다.
- 생성 경로(WS `_stream_summary`, REST summarize Lambda)는 회의 레코드의 사본만 읽고, `summary_styles.json`의 공통 래퍼로 프롬프트를 조립한다.

**Tech Stack:** Python 3 / FastAPI WS 백엔드(aiobotocore), TypeScript Lambda(NodejsFunction), AWS CDK, DynamoDB, Next.js(React), node:test + pytest.

**Spec:** `docs/superpowers/specs/2026-10-28-custom-summary-templates-design.md`

## Global Constraints

- 본인 전용. 사용자 식별은 항상 Cognito claims `sub`(REST) / `state.user_id`(WS). 남의 양식·회의는 없는 것과 같은 404.
- 테이블 `transmeet-summary-templates`: PK `userId`(S), SK `templateId`(S, UUID), PAY_PER_REQUEST, `pointInTimeRecovery: true`, `RemovalPolicy.RETAIN`.
- 제한: `name` 1~30자(trim 후), `instruction` 본문 1~6,000자, `example` 본문 1~12,000자, 사용자당 10개. 넘으면 400(개수는 409). 자르지 않는다.
- 회의 선택값 `summaryStyle = "custom"`. 사본 `summaryCustom = { templateId, name, kind, body, templateUpdatedAt }`. 생성 라벨 `summaryGeneratedLabel`(커스텀일 때만).
- 요청 `summaryStyle`이 레코드보다 우선한다(현행 유지). 커스텀 요청은 `"custom"` 값만 보내고 본문은 보내지 않는다. 요청이 `custom`인데 사본이 없으면 `detailed`로 생성한다.
- 기존 네 방식(`detailed`, `amazon`, `decisions`, `report`)의 프롬프트 문자열은 한 글자도 바뀌지 않는다.
- 한 줄 요약(`useMeetingBrief`)과 미팅 페이지에는 적용하지 않는다.
- 편집기 안내 문구: "메일에서는 제목·불릿·표·문단만 그대로 보입니다".
- 백엔드 배포: 고유 이미지 태그 푸시 → `cd infra && npx cdk deploy -c wsImageTag=<태그> --require-approval never --outputs-file outputs.json` → `./deploy.sh frontend`.
- 커밋은 파일을 지정해 `git add`한다. 작업 트리에 다른 세션의 미커밋 변경이 섞여 있다.

## Review Focus

1. 예시 회의록 붙여넣기에서 예시의 이름·수치·결정이 요약에 새어 들어가는 것.
   - 기대: 구조와 섹션 제목만 따르고, 내용은 이번 트랜스크립트에서만 가져온다.
   - Task 7에서 실제 모델로 확인한다.
2. 양식을 지운 뒤에도 그 양식을 고른 회의의 자동 요약·종료 메일이 계속 동작해야 한다.
   - 생성 경로가 템플릿 테이블을 읽지 않는다는 것을 Task 4 테스트로 고정한다.
3. 다른 사용자의 `templateId`로 선택·수정·삭제를 시도하는 경우.
   - 기대: 404. 사본도 생기지 않는다.
   - Task 2, Task 3 테스트.
4. 본문이 정확히 상한 길이인 경우와 1자 넘는 경우.
   - 기대: 12,000자는 저장되고 12,001자는 400. 공백만 있는 이름과 본문도 400.
   - Task 2 테스트.
5. 커스텀 → 기본 방식 → 같은 커스텀 순서로 다시 고르는 경우.
   - 기대: 사본이 최신 양식으로 갱신된다.
   - 사본이 오래됐을 때 "최신 내용 적용" 링크가 보이고, 누르면 갱신된다.
   - Task 3, Task 6 테스트.

---

### Task 1: 공통 프롬프트 조립(커스텀 래퍼) + 기준점 커밋

**Files:**
- Modify: `backend/app/summary_styles.json`
  - `customWrapper`(string[])와 `exampleWrapper`(string[])를 추가한다.
  - `prompts` 키는 그대로 둔다.
- Modify: `backend/app/summary_prompt.py`
- Modify: `infra/lambda/shared/summaryPrompt.ts`
- Modify: `src/lib/summaryStyles.ts`
- Test: `backend/tests/test_summary_prompt.py`, `tests/summaryStyles.test.mjs`

**Interfaces:**
- Produces (TS, `src/lib/summaryStyles.ts`):
  - `type BuiltinSummaryStyle = (typeof SUMMARY_STYLES)[number]['value']`
  - `type SummaryStyle = BuiltinSummaryStyle | 'custom'`
  - `isSummaryStyle(v): v is SummaryStyle`: `'custom'`도 true.
  - `isBuiltinSummaryStyle(v): v is BuiltinSummaryStyle`
  - `normalizeSummaryStyle(v): SummaryStyle`: `'custom'`을 유지한다.
  - `summaryStyleLabel(v: unknown, customName?: string): string`: custom이면 `customName ?? '내 양식'`.
  - `type SummaryTemplateKind = 'instruction' | 'example'`
  - `interface SummaryCustom { templateId: string; name: string; kind: SummaryTemplateKind; body: string; templateUpdatedAt: string }`
  - `SUMMARY_TEMPLATE_LIMITS = { nameMax: 30, instructionMax: 6000, exampleMax: 12000, perUser: 10 } as const`
- Produces (TS, `infra/lambda/shared/summaryPrompt.ts`):
  - `buildSummaryPrompt(title: string, transcript: string, style?: unknown, custom?: SummaryCustom | null): string`
  - `resolveSummaryStyle(requested: unknown, saved: unknown, custom: SummaryCustom | null | undefined): SummaryStyle`
  - `parseSummaryCustom(v: unknown): SummaryCustom | null`: kind와 본문 길이를 검증하고, 아니면 null.
- Produces (Python, `app/summary_prompt.py`):
  - `build_summary_prompt(title, transcript, style=DEFAULT, custom: Optional[dict] = None) -> str`
  - `resolve_summary_style(requested, saved, custom) -> str`
  - `parse_summary_custom(attr: Optional[dict]) -> Optional[dict]`: DynamoDB `{"M": {...}}` 형식을 받는다.
  - `normalize_summary_style`는 `"custom"`을 유효값으로 인정한다.

- [ ] **Step 1: 기준점 커밋.**
  - `git status`로 요약 방식 관련 미커밋 파일을 확인한다.
  - 다음 파일만 지정해 커밋한다: `backend/app/summary_styles.json`, `backend/app/summary_prompt.py`, `src/lib/summaryStyles.ts`, `infra/lambda/shared/summaryPrompt.ts`, `src/components/SummaryPanel.tsx`, `README.md`, 관련 테스트.
  - 메시지: `feat(summary): 요약 방식 선택(자세하게·Amazon 미팅록·의사결정 기록·공유·보고용)`
  - 다른 파일이 같은 hunk에 섞여 있으면 사용자에게 알리고 해당 파일은 건너뛴다.

- [ ] **Step 2: 실패하는 테스트 작성** (`backend/tests/test_summary_prompt.py`)

```python
CUSTOM = {"templateId": "t1", "name": "주간 회의", "kind": "instruction",
          "body": "섹션은 이슈 / 원인 / 조치", "templateUpdatedAt": "2026-10-28T00:00:00Z"}

def test_custom_prompt_wraps_user_format_with_fixed_rules():
    p = build_summary_prompt("주간", "원문", style="custom", custom=CUSTOM)
    assert "<사용자_양식>\n섹션은 이슈 / 원인 / 조치\n</사용자_양식>" in p
    assert "트랜스크립트에서만" in p and "고정 규칙이 우선" in p
    assert p.endswith("\n---\n원문") and "미팅 제목: 주간" in p

def test_example_prompt_forbids_copying_example_content():
    p = build_summary_prompt("주간", "원문", style="custom", custom={**CUSTOM, "kind": "example", "body": "## 이슈\n- A팀 3억"})
    assert "옮기지 않습니다" in p and "<사용자_양식>\n## 이슈\n- A팀 3억\n</사용자_양식>" in p

@pytest.mark.parametrize(("requested", "saved", "custom", "expected"), [
    ("custom", "detailed", CUSTOM, "custom"), (None, "custom", CUSTOM, "custom"),
    ("custom", "custom", None, "detailed"), ("report", "custom", CUSTOM, "report"), ("bogus", "custom", CUSTOM, "detailed"),
])
def test_resolve_summary_style(requested, saved, custom, expected):
    assert resolve_summary_style(requested, saved, custom) == expected

def test_builtin_prompts_unchanged_by_custom_argument():
    for s in ("detailed", "amazon", "decisions", "report"):
        assert build_summary_prompt("t", "x", s, custom=CUSTOM) == build_summary_prompt("t", "x", s)

def test_parse_summary_custom_rejects_bad_kind_and_oversize():
    m = lambda kind, body: {"M": {"templateId": {"S": "t1"}, "name": {"S": "n"}, "kind": {"S": kind},
                                  "body": {"S": body}, "templateUpdatedAt": {"S": "x"}}}
    assert parse_summary_custom(m("instruction", "a"))["body"] == "a"
    assert parse_summary_custom(m("other", "a")) is None
    assert parse_summary_custom(m("example", "a" * 12001)) is None
    assert parse_summary_custom(None) is None
```

  - `("bogus", "custom", …)` 행이 detailed인 이유: 요청 값은 정규화한 뒤에 우선하고, 알 수 없는 값은 detailed다(현행 동작).

- [ ] **Step 3: 실행해 실패 확인.** `cd backend && python -m pytest tests/test_summary_prompt.py -q` → 새 테스트 FAIL(ImportError 또는 assert).

- [ ] **Step 4: 구현.**
  - **JSON 래퍼**:
    - `customWrapper`는 다음 문장을 담는다.
      - 사실은 트랜스크립트에서만 가져온다.
      - 없는 담당, 기한, 수치는 추측하지 않는다.
      - 화자 정보가 없으니 화자 라벨을 만들지 않는다.
      - 짧게 쓰더라도 근거와 영향은 남긴다.
      - 형식, 섹션, 톤, 길이는 아래 `<사용자_양식>`을 따른다.
      - 사용자 양식과 이 규칙이 충돌하면 고정 규칙이 우선한다.
    - `exampleWrapper`는 다음 문장을 담는다.
      - 아래는 형식 견본이다. 구조, 섹션 제목, 톤, 길이감만 따른다.
      - 견본 속 이름, 수치, 결정, 일정은 절대 요약에 옮기지 않는다.
      - 견본의 섹션 중 이번 회의에 해당 내용이 없는 섹션은 "해당 없음"으로 둔다.
  - **조립 순서**:
    - 지시문: `head + customWrapper + "<사용자_양식>\n{body}\n</사용자_양식>" + "\n---\n" + transcript`
    - 예시: wrapper 뒤에 `exampleWrapper`를 덧붙이고 나머지는 같다.
  - **TS**: `shared/summaryPrompt.ts`가 같은 JSON으로 같은 문자열을 만든다.

- [ ] **Step 5: Python↔TS 동기화 테스트 확장.**
  - `tests/summaryStyles.test.mjs`의 'Python and REST build the same prompts' 테스트에 다음을 추가한다.
    - 커스텀 두 종류(instruction, example)의 `build_summary_prompt` 결과 비교.
    - `resolve_summary_style` 표 5행을 TS `resolveSummaryStyle`과 비교.
  - `normalizeSummaryStyle('custom') === 'custom'`과 `summaryStyleLabel('custom', '주간 회의') === '주간 회의'`를 단언한다.

- [ ] **Step 6: 실행해 통과 확인.** `cd backend && python -m pytest -q` 와 `npm run -s test:summary` → 전부 PASS.

- [ ] **Step 7: 커밋.** `git add` 위 파일들 → `feat(summary): 커스텀 요약 양식 프롬프트 조립`

---

### Task 2: 양식 저장소 — 테이블 + `SummaryTemplatesFn` + `/me/summary-templates`

**Files:**
- Create: `infra/lambda/summary-templates/index.ts`
- Modify: `infra/lib/transmeet-stack.ts`
  - 테이블을 추가하고 `commonEnv.SUMMARY_TEMPLATES_TABLE`를 넣는다.
  - `lambdaRole`에 grantReadWriteData를 준다.
  - 함수, 통합, 리소스를 추가한다. 리소스는 기존 `me` 리소스를 재사용해 `addResource('summary-templates')`, `{templateId}`로 만든다.
- Create: `tests/summaryTemplates.test.mjs`
- Modify: `package.json` → `test:summary`에 `tests/summaryTemplates.test.mjs`를 추가한다.

**Interfaces:**
- Consumes: `SUMMARY_TEMPLATE_LIMITS`, `SummaryTemplateKind` (Task 1)
- Produces (HTTP):
  - `GET /me/summary-templates` → `200 { templates: SummaryTemplate[] }` (`updatedAt` 내림차순)
  - `POST` body `{ name, kind, body }` → `201 SummaryTemplate`
  - `PUT /me/summary-templates/{templateId}` body 같은 모양 → `200 SummaryTemplate`
  - `DELETE` → `204`
  - `SummaryTemplate = { templateId, name, kind, body, createdAt, updatedAt }`
- Produces (TS): `export function validateTemplateInput(v: unknown): { name: string; kind: SummaryTemplateKind; body: string } | { error: string }`. 같은 파일에서 export해 Task 6 편집기가 재사용한다.

- [ ] **Step 1: 실패하는 테스트 작성.**
  - fake DDB는 `summaryStyles.test.mjs`의 `setup` 패턴을 따른다. `QueryCommand`, `PutCommand`, `UpdateCommand`, `DeleteCommand`를 메모리 Map으로 처리한다.
  - 테스트:
    - `create → list → update → delete round trip for the caller only`
    - `another user's templateId is 404 for PUT and DELETE and absent from GET`
      - user-2가 user-1의 id로 PUT/DELETE하면 404이고 항목은 그대로다.
    - `limits`
      - 이름 `' '` → 400, 이름 31자 → 400.
      - instruction 6,000자 → 201, 6,001자 → 400.
      - example 12,000자 → 201, 12,001자 → 400.
      - `kind: 'other'` → 400.
      - 11번째 생성 → 409.
    - `malformed JSON body → 400, unauthenticated (no sub) → 401`

- [ ] **Step 2: 실행해 실패 확인.** `node --test tests/summaryTemplates.test.mjs` → 모듈이 없어 FAIL.

- [ ] **Step 3: 구현 `handler(event)`.**
  - PUT과 DELETE는 `ConditionExpression: 'attribute_exists(templateId)'`를 쓴다. 키가 `(userId, templateId)`라서 소유권은 키가 보장한다. ConditionalCheckFailed는 404로 응답한다.
  - 개수는 생성 전 `Query`의 `Select: 'COUNT'`로 센다.
  - CORS 헤더는 `email` Lambda와 같다.

- [ ] **Step 4: CDK.**
  - `SummaryTemplatesTable`을 Global Constraints 설정대로 만든다.
  - `SummaryTemplatesFn`은 timeout 10초, `...commonNodejsFunctionProps`.
  - 메서드 4개에 `authed`를 단다.
  - 확인: `cd infra && npx cdk synth > /dev/null` 성공.

- [ ] **Step 5: 실행해 통과 확인.** `npm run -s test:summary` → PASS.

- [ ] **Step 6: 커밋.** `feat(summary): 내 양식 저장 API`

---

### Task 3: 회의 선택 시 사본 복사 + REST 요약 Lambda의 커스텀 생성

**Files:**
- Modify: `infra/lambda/meetings/index.ts:284-304`
  - summary-style PUT에 커스텀 분기를 추가한다.
  - GET 응답이 이미 회의 전체를 돌려주는지 확인하고, `summaryCustom`과 `summaryGeneratedLabel`이 포함되게 한다.
- Modify: `infra/lambda/summarize/index.ts:190-252`
- Test: `tests/summaryStyles.test.mjs`

**Interfaces:**
- Consumes: `isBuiltinSummaryStyle`, `SummaryCustom` (Task 1), 테이블 env `SUMMARY_TEMPLATES_TABLE` (Task 2), `resolveSummaryStyle` / `buildSummaryPrompt` / `parseSummaryCustom` (Task 1)
- Produces (HTTP):
  - `PUT /meetings/{id}/summary-style` body `{ summaryStyle: 'custom', templateId }` → `200 { meetingId, summaryStyle: 'custom', summaryCustom }`
  - 양식이 없거나 남의 것이면 404. 기본 방식 요청은 지금 응답 그대로.
  - summarize 응답에 `summaryGeneratedLabel?: string`을 더한다(커스텀일 때만).

- [ ] **Step 1: 실패하는 테스트 작성** (`summaryStyles.test.mjs`의 `setup`에 템플릿 레코드 Map을 추가)
  - `selecting a custom template copies it server-side and ignores client-sent body`
    - 요청 바디에 `body: '해킹'`을 넣어도 저장된 사본 body는 테이블 값이다.
    - `templateUpdatedAt`은 템플릿의 `updatedAt`이다.
  - `selecting another user's template is 404 and leaves the meeting unchanged`
  - `reselecting after an edit refreshes the snapshot`
    - 템플릿 body를 바꾼 뒤 같은 PUT을 다시 보내면 사본이 갱신된다.
  - `REST summary with custom snapshot uses it and stores the label`
    - 모델 요청 텍스트에 `<사용자_양식>`이 있다.
    - 저장된 `summaryGeneratedStyle === 'custom'`, `summaryGeneratedLabel === 템플릿 이름`.
  - `REST summary with custom but no snapshot falls back to detailed`
    - `summaryGeneratedLabel`은 REMOVE된다(또는 저장되지 않는다).

- [ ] **Step 2: 실행해 실패 확인.** `npm run -s test:summary` → FAIL.

- [ ] **Step 3: 구현.**
  - **meetings Lambda**:
    - `GetCommand(SUMMARY_TEMPLATES_TABLE, { userId, templateId })`로 양식을 읽는다.
    - `UpdateCommand`로 `SET summaryStyle = :c, summaryCustom = :snap`. 기존 소유 조건식은 그대로 둔다.
  - **summarize Lambda**:
    - `resolveSummaryStyle(body.summaryStyle, item.summaryStyle, parseSummaryCustom(item.summaryCustom))`로 방식을 정한다.
    - 프롬프트는 `buildSummaryPrompt(title, transcript, style, custom)`.
    - 저장은 커스텀이면 `SET … summaryGeneratedLabel = :label`, 아니면 `REMOVE summaryGeneratedLabel`.
  - meetings Lambda 함수 role은 이미 `lambdaRole`(Task 2에서 grant 완료)이다.

- [ ] **Step 4: 실행해 통과 확인.** `npm run -s test:summary` → PASS.

- [ ] **Step 5: 커밋.** `feat(summary): 회의별 내 양식 선택과 REST 요약 적용`

---

### Task 4: WS 백엔드 `_stream_summary`의 커스텀 생성

**Files:**
- Modify: `backend/app/ws_handler.py:1196-1320`
  - 메타 조회 `ProjectionExpression`에 `summaryCustom`을 추가하고, 전체 조회 경로에서도 읽는다.
  - 스타일은 `resolve_summary_style`로 정하고, 프롬프트에 custom을 전달한다.
  - delta/done 이벤트와 DynamoDB 저장에 라벨을 넣는다.
- Test: `backend/tests/test_stream_summary.py`

**Interfaces:**
- Consumes: `resolve_summary_style`, `parse_summary_custom`, `build_summary_prompt(..., custom=)` (Task 1)
- Produces (WS 이벤트):
  - `summary_stream` delta/done에 `summaryStyle`이 들어가고, 커스텀이면 `summaryLabel: <이름>`이 추가된다.
  - DynamoDB: 커스텀이면 `SET summaryGeneratedLabel`, 아니면 `REMOVE summaryGeneratedLabel`.

- [ ] **Step 1: 실패하는 테스트 작성** (기존 `FakeDDB`, `FakeBedrock`, `install` 재사용)
  - `custom snapshot on the meeting drives the prompt and label (inline and stored paths)`
    - 두 경로 모두 bedrock 호출 텍스트에 `<사용자_양식>`이 있다.
    - done 이벤트 `summaryLabel == "주간 회의"`.
    - update 표현식에 `summaryGeneratedLabel`이 있다.
  - `custom requested without snapshot generates detailed and removes label`
  - `generation never reads the templates table`
    - FakeDDB가 `TableName != config.MEETINGS_TABLE`인 호출을 받으면 실패하게 만든다.
    - 커스텀 생성이 성공하는지 본다. 양식을 지워도 동작한다는 보장이다.

- [ ] **Step 2: 실행해 실패 확인.** `cd backend && python -m pytest tests/test_stream_summary.py -q` → FAIL.

- [ ] **Step 3: 구현.** 회의 종료(`_end_meeting`)는 `_stream_summary`를 그대로 부르므로 따로 바꿀 것이 없다. 종료 테스트(`test_meeting_end.py`)가 통과하는지만 확인한다.

- [ ] **Step 4: 실행해 통과 확인.** `cd backend && python -m pytest -q` → 전부 PASS.

- [ ] **Step 5: 커밋.** `feat(summary): WS 요약에 내 양식 적용`

---

### Task 5: 프런트 데이터 계층 — API 클라이언트, 타입, 상태

**Files:**
- Modify: `src/lib/api.ts`
  - `toMeeting`이 `summaryCustom`(검증)과 `summaryGeneratedLabel`을 읽게 한다.
  - `api.summaryTemplates` 클라이언트를 추가한다.
  - `updateSummaryStyle(id, style, templateId?)`로 바꾼다.
- Modify: `src/types/meeting.ts`: `summaryCustom?: SummaryCustom`, `summaryGeneratedLabel?: string`
- Create: `src/hooks/useSummaryTemplates.ts`
- Modify: `src/app/page.tsx`
  - `handleSummaryStyleChange(style, templateId?)`로 바꾼다.
  - `summary_stream` 처리에서 `msg.summaryLabel`을 `summaryGeneratedLabel`로 반영한다.
  - REST 응답 라벨도 반영한다.
- Modify: `src/lib/websocket.ts`: 이벤트 타입에 `summaryLabel?: string`을 추가한다.
- Test: `tests/summaryStyles.test.mjs`

**Interfaces:**
- Consumes: HTTP from Task 2·3, `SummaryCustom` (Task 1)
- Produces:
  - `api.summaryTemplates.list(): Promise<SummaryTemplate[]>`
  - `api.summaryTemplates.create(input)`, `update(id, input)`, `remove(id)`
  - `useSummaryTemplates(enabled: boolean): { templates: SummaryTemplate[]; refresh(): Promise<void>; save(input, id?): Promise<SummaryTemplate>; remove(id): Promise<void>; error?: string }`
  - `export type SummaryTemplate`는 `src/lib/summaryStyles.ts`에 둔다.
  - page → SummaryPanel props:
    - `templates: SummaryTemplate[]`
    - `summaryCustom?: SummaryCustom`
    - `summaryGeneratedLabel?: string`
    - `onStyleChange(style: SummaryStyle, templateId?: string)`
    - `onManageTemplates(): void`

- [ ] **Step 1: 실패하는 테스트 작성.**
  - `toMeeting keeps a valid custom snapshot and label, drops malformed snapshots`
    - kind가 이상하거나 body가 숫자인 사본은 undefined.
    - `summaryStyle: 'custom'`은 유지된다.
  - `a stop-triggered summary sends 'custom' without the template body`
    - 기존 handleSummarize 추출 테스트를 확장한다.
    - `sent[0][2] === 'custom'`이고, 전송 페이로드에 body 문자열이 없다.

- [ ] **Step 2: 실행해 실패 확인.** `npm run -s test:summary` → FAIL.

- [ ] **Step 3: 구현.**
  - 템플릿 목록은 로그인이 끝나고 `HAS_API`일 때 한 번 불러온다. 저장·삭제 후에는 갱신한다.
  - 저장 실패 시 되돌림과 저장 중 잠금은 기존 `handleSummaryStyleChange` 흐름을 그대로 쓴다.

- [ ] **Step 4: 실행해 통과 확인.** `npm run -s test:summary && npx tsc --noEmit -p .` → PASS.

- [ ] **Step 5: 커밋.** `feat(web): 내 양식 데이터 계층`

---

### Task 6: UI — 드롭다운, 양식 관리 탭, 편집기

**Files:**
- Modify: `src/components/SummaryPanel.tsx:46-72, 112`
- Create: `src/components/SummaryTemplateSettings.tsx`: 목록과 편집기.
- Modify: `src/components/SettingsPanel.tsx:148-157`
  - 탭 `{ value: 'summary', label: '요약 양식', icon: <기존 아이콘 중 문서류> }`를 추가한다.
  - 외부에서 탭을 지정해 열 수 있게 `initialTab?: 'general' | 'theme' | 'summary'` prop을 추가한다.
- Modify: `src/app/page.tsx`: `onManageTemplates`가 설정 패널을 `initialTab='summary'`로 연다.
- Modify: `tests/summary_styles_browser.py`: 커스텀 흐름을 추가한다.

**Interfaces:**
- Consumes: Task 5의 props와 훅. 편집기 검증에는 `validateTemplateInput`(Task 2)과 같은 규칙인 `SUMMARY_TEMPLATE_LIMITS`를 쓴다.

- [ ] **Step 1: 브라우저 테스트 작성** (`tests/summary_styles_browser.py`의 `choices` 시나리오 뒤에 `custom` 시나리오)
  - 목업 API가 템플릿 1개를 돌려준다.
  - 드롭다운에 `optgroup[label="내 양식"]`과 그 이름의 option이 있다. 선택하면 PUT 바디가 `{summaryStyle:'custom', templateId}`다.
  - "내 양식 관리…"를 고르면 설정 패널의 '요약 양식' 탭이 열린다. 선택값은 바뀌지 않는다.
  - 회의 사본의 `templateUpdatedAt`이 템플릿 `updatedAt`보다 오래됐으면 "양식이 수정됐습니다 · 최신 내용 적용" 링크가 보인다. 클릭하면 같은 PUT이 1회 나간다.
  - 사본의 templateId가 목록에 없으면 option 라벨이 `"<이름> (삭제된 양식)"`이다.
  - 생성 표시줄에 `summaryGeneratedLabel`이 보인다.
  - 편집기:
    - 종류 토글이 있다(지시문 작성 / 기존 회의록 붙여넣기).
    - 글자 수가 `n / 6000`, 예시는 `n / 12000`으로 보인다.
    - 상한을 넘으면 저장 버튼이 disabled다.
    - 안내 문구 "메일에서는 제목·불릿·표·문단만 그대로 보입니다"가 보인다.

- [ ] **Step 2: 구현.**
  - "내 양식 관리…" option 값은 `__manage__`로 둔다. 이 값을 고르면 `onManageTemplates()`를 호출하고, select 값은 현재 선택으로 되돌린다.
  - 기존 선택지 순서와 `' (기본)'` 표기는 유지한다.
  - placeholder:
    - 지시문: `예) 섹션은 이슈 / 원인 / 조치 / 담당. 각 이슈는 표로, 조치에는 기한을 붙여 주세요.`
    - 예시: `팀에서 쓰던 회의록을 그대로 붙여 넣으세요. 구조와 톤만 따르고 내용은 옮기지 않습니다.`

- [ ] **Step 3: 확인.**
  - `npx tsc --noEmit -p . && npx eslint src/components/SummaryPanel.tsx src/components/SummaryTemplateSettings.tsx src/components/SettingsPanel.tsx && npm run build` → 성공.
  - 로컬 서버를 띄우고 `TRANSMEET_TEST_URL=http://127.0.0.1:3186 python tests/summary_styles_browser.py` → 전부 통과.

- [ ] **Step 4: 커밋.** `feat(web): 내 양식 선택·관리 UI`

---

### Task 7: 실제 모델 확인 + 배포

**Files:**
- 없음. 결과는 `/tmp/sumcmp/custom/`에 둔다.

- [ ] **Step 1: 실제 모델 확인.**
  - `/tmp/sumcmp/v3/run2.py` 방식(미팅 `68dd601d-c80d-4722-ad3a-88bf1c69f803`, 운영 설정 Opus 5.5 / effort low)으로 두 양식을 돌린다.
    - (a) 지시문 `섹션은 이슈 / 원인 / 조치 / 담당, 이슈별 표`
    - (b) 다른 회사 회의록 예시. 고유명사 `한빛전자`, 수치 `3억 원`, 결정 `11월 출시 확정`을 넣는다.
  - 통과 기준:
    - (a) 지정한 섹션 제목이 그대로 나온다.
    - (b) 출력에 `한빛전자`, `3억`, `11월 출시`가 하나도 없고, 섹션 구조는 예시와 같다.
    - 둘 다 담당을 추측하지 않는다(미정 유지).
  - 실패하면 Task 1 래퍼 문구를 보강하고 다시 돌린다.

- [ ] **Step 2: 전체 테스트.** `cd backend && python -m pytest -q && cd .. && npm run -s test:summary && npx tsc --noEmit -p .` → 전부 PASS.

- [ ] **Step 3: 배포.**
  - 고유 태그 `summary-templates-<YYYYMMDD-HHMM>`로 이미지를 빌드해 푸시한다(`--platform linux/amd64`).
  - `cdk deploy -c wsImageTag=<태그>`로 새 테이블, Lambda, API가 생긴다.
  - ECS 태스크 이미지 태그가 일치하는지 확인한다.
  - `./deploy.sh frontend`.
  - 확인: 배포 후 `aws dynamodb describe-table --table-name transmeet-summary-templates --query Table.TableStatus` → `ACTIVE`.
