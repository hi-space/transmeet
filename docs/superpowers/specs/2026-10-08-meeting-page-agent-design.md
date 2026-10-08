# 미팅 페이지 에이전트 (AgentCore Harness) 설계

- 작성일: 2026-10-08
- 상태: 사용자 리뷰 대기
- 범위: 미팅 대화 기록으로 공유 가능한 HTML 미팅록 페이지를 생성하는 에이전트와, 그 페이지를 확인·공유하는 UI

## 1. 목표와 비목표

### 목표

1. 기존 마크다운 요약(`_stream_summary`, Haiku)은 그대로 두고, **별도 에이전트**가 미팅록 **단일 HTML 페이지**를 만든다.
2. 에이전트는 **Amazon Bedrock AgentCore Harness**로 구성한다. 구성 요소는 **스킬 4개 + Claude Opus 5.5 + Code Interpreter 내장 도구 + Web Search 도구(AgentCore Gateway 커넥터)**.
3. 페이지 포맷·내용의 **통일성은 스킬이 보장**한다. 스킬은 `skill-creator`로 만들고 샘플 트랜스크립트로 검증한 뒤 S3에 올린다.
4. 생성된 페이지는 **공개 URL(추측 불가 ID)** 로 발행되고, 앱의 **Share 버튼**으로 링크를 복사·공유한다.
5. 앱에 **미팅 페이지 영역**(생성 진행 상황, 미리보기, 공유, 재생성, 공유 해제)을 추가한다.

### 비목표 (이번 범위 밖)

- PDF/DOCX 내보내기, 페이지 편집, 페이지 버전 히스토리 (미팅당 최신 1개만 유지)
- 페이지 열람 인증 (공개 URL 모델을 선택했음)
- Web Search 외 추가 MCP 서버 (하네스 정의에 슬롯만 남김)
- 요약·번역 등 기존 기능 변경

## 2. 사용자가 확정한 결정

| 항목 | 결정 |
|---|---|
| 페이지 언어 | 한국어 본문. 인용과 핵심 발언은 원문(영어)과 번역 병기 |
| 공유 방식 | 공개 URL, 추측 불가 ID. 페이지별 공유 해제(삭제) 가능 |
| IaC | 기존 CDK 스택에 `CfnResource`로 Harness 선언 (CDK 2.243에 L1 없음) |
| 페이지 구성 | 전체 트랜스크립트와 참여 통계는 **제외**. 미팅 흐름을 따라 논의 내용을 **빠짐없이** 기록. 관련 **외부 공식 링크 리서치·더블 체크**와 **인사이트 보고서** 추가 |
| 스킬 수 | 하나가 아닌 **여러 스킬 조합**. 논의 내용을 설명하는 **다이어그램 스킬** 포함 |

## 3. 미팅 페이지 내용 (스킬이 강제하는 구조)

페이지는 아래 섹션을 이 순서로 가진다. 각 섹션 루트 요소에 `data-section="<id>"` 속성을 둬서 검증 스크립트가 존재 여부를 확인한다.

| 순서 | id | 내용 | 작성 규칙 |
|---|---|---|---|
| 1 | `header` | 제목, 날짜·시작/종료·소요시간, 참석자(화자 라벨), 언어 쌍, 생성 시각 | 메타데이터는 백엔드가 입력으로 넘김. 추정 금지 |
| 2 | `tldr` | 한눈에 보기 3~5줄 | 결정·결과 중심. 형용사 최소화 |
| 3 | `decisions` | 결정 사항 목록 | 각 항목에 근거 발언 시각(`[HH:MM]`) 표기 |
| 4 | `action-items` | 담당 / 할 일 / 기한 / 상태 표 | 담당·기한 불명은 "미정". 추측 금지 |
| 5 | `discussion` | **미팅 흐름을 따르는 논의 기록**. 시간순 주제 블록. 각 블록에 시각 범위, 논의 요지, 주요 발언 인용(원문+번역), 제기된 질문과 답 | 트랜스크립트의 모든 주제가 최소 한 블록에 매핑되어야 함. 사소한 잡담만 생략 가능. 섹션 상단에 주제 타임라인 다이어그램 1개, 블록 안에는 `meeting-diagrams` 스킬 기준에 맞는 논의(흐름·구조·비교·관계)에만 다이어그램 삽입 |
| 6 | `open-questions` | 미결 사항, 후속 확인 필요 항목 | 누가 확인하는지 적혀 있으면 표기 |
| 7 | `references` | **관련 공식 자료**. 미팅에서 언급된 제품·서비스·규격·수치·일정에 대해 Web Search로 찾은 공식 출처(벤더 문서, 표준 기구, 공식 블로그·발표) | 항목당 제목, URL, 1줄 요약, 미팅 발언과의 관계. **더블 체크 결과** 표시: `확인됨` / `상이함(미팅 내용 vs 출처)` / `확인 불가` |
| 8 | `insights` | **인사이트 보고서**. 시사점, 리스크, 기회, 권장 후속 조치, 다음 미팅 제안 안건 | 사실(트랜스크립트·출처)과 추론을 시각적으로 구분(추론 문장에 "추정" 배지). 각 인사이트에 근거 섹션 앵커 링크 |
| 9 | `glossary` | 영↔한 핵심 용어·약어 | 미팅에 실제 등장한 것만 |
| 10 | `footer` | "TransMeet로 생성", 모델명, 미팅 ID, 생성 시각, 참고 자료 조회일 | |

형식 제약:

- **단일 self-contained HTML**. 외부 CSS/JS/폰트/이미지 요청 0개. 이미지는 인라인 SVG만.
- **JavaScript 금지**. 접기/펼치기는 `<details>`로. (iframe을 완전 샌드박스로 둘 수 있게 함)
- `<html lang="ko">`, 반응형(모바일 360px~), `prefers-color-scheme` 다크모드, 인쇄 스타일.
- `<meta name="transmeet:meeting-id">`, `<meta name="transmeet:generated-at">`, `<meta name="transmeet:model">` 포함.
- 크기 상한 1.5 MB.

## 4. 아키텍처

```
브라우저 ──WS: generate_page──▶ ECS FastAPI (ws_handler)
                                   │ InvokeHarness (stream)
                                   ▼
                        AgentCore Harness "transmeet_meeting_page"
                        ├─ model: us.anthropic.claude-opus-5-5
                        ├─ skills (S3): meeting-minutes-page / meeting-research / meeting-insights / meeting-diagrams
                        ├─ tool: agentcore_code_interpreter  (HTML 검증, 다이어그램 스크립트 실행)
                        ├─ tool: agentcore_gateway → Web Search 커넥터 (공식 자료 리서치)
                        └─ tool: inline_function publish_page ─┐ (stopReason=tool_use로 백엔드에 핸드오프)
                                                              ▼
                               ECS FastAPI: S3 pages 버킷에 업로드 → DynamoDB meeting.page 갱신
                                                              │ toolResult(url) 반환
                                                              ▼
                               하네스가 마무리 메시지 생성 → page_stream done
브라우저 ◀── page_stream status/delta/done ──
사용자 ──▶ https://transmeet.hi-yoo.com/p/<meetingId>/<pageId>.html (CloudFront /p/* → pages 버킷)
```

### 4.1 왜 이 구조인가

- **ECS 경유**: 생성은 1~5분 걸린다. REST Lambda(API Gateway 29초)로는 불가하고, 기존 `summarize` 액션과 같은 WebSocket 스트리밍 패턴을 재사용한다.
- **inline_function으로 발행**: 업로드 경로, ID 생성, DynamoDB 갱신을 에이전트가 아닌 백엔드가 결정한다. 하네스 실행 역할에 S3 쓰기 권한을 줄 필요가 없고, 발행이 결정적이다.
- **Web Search는 Gateway 커넥터**: 외부 API 키 없이 AWS 안에서 처리된다. `us-east-1`에서 사용 가능. 하네스에는 `agentcore_gateway` 도구로 붙인다.
- **스킬은 S3**: `deploy.sh skills`로 sync하면 하네스 재배포 없이 포맷을 수정할 수 있다. 하네스는 세션 시작 시 스킬을 fetch한다.
- **메모리 비활성**: 미팅마다 독립 생성이어야 하고, 이전 미팅 내용이 섞이면 안 된다. `memory: {disabled: {}}`.

## 5. 구성 요소

### 5.1 스킬 (`agent/skills/`)

| 스킬 | 역할 | 번들 리소스 |
|---|---|---|
| `meeting-minutes-page` | 페이지 구조·스타일·작성 규칙(3장). 작업 순서(입력 해석 → 섹션 초안 → 리서치·인사이트·다이어그램 스킬 호출 → 조립 → 검증 → publish_page) | `assets/template.html`(디자인 토큰·레이아웃 골격), `references/section-rules.md`, `scripts/validate_page.py`(필수 섹션·외부 리소스·JS·메타·크기·SVG 접근성 텍스트 검사, 종료 코드로 결과) |
| `meeting-research` | 리서치 대상 추출(제품·서비스·규격·수치·일정·인물 직함), Web Search 쿼리 작성, 공식 출처 우선순위(벤더 docs > 표준 기구 > 공식 블로그 > 보도자료 > 기타), 더블 체크 판정 기준, `references` 섹션 출력 포맷 | `references/source-priority.md`, `references/output-format.md` |
| `meeting-insights` | 인사이트 보고서 작성법: 사실/추론 구분, 리스크·기회·후속 조치 프레임, 근거 링크 규칙, 과장 금지 | `references/insight-frames.md`, `references/output-format.md` |
| `meeting-diagrams` | 논의 내용을 설명하는 **인라인 SVG 다이어그램**. 언제 그릴지 판단 기준(순서·분기가 있는 흐름, 구성요소 관계, 선택지 비교, 시간 구간)과 언제 그리지 않을지(단순 나열, 2개 이하 항목). 종류별 레이아웃 규칙: 타임라인, 흐름도(결정 분기), 관계도(구성요소·시스템), 비교표형 차트. 페이지 디자인 토큰과 동일한 색·폰트, 다크모드 대응(`currentColor`·CSS 변수), `<title>`/`<desc>` 접근성 텍스트, 페이지당 최대 6개, 각 다이어그램 아래 1문장 캡션 | `references/diagram-types.md`, `scripts/timeline_svg.py`(JSON 구간 → 타임라인 SVG), `scripts/flow_svg.py`(노드·엣지 JSON → 자동 배치 흐름도 SVG), `assets/diagram-tokens.css` |

제작 절차는 `skill-creator`를 따른다. 샘플 트랜스크립트 3개(짧은 1:1, 다자 의사결정 회의, 기술 리뷰)를 `agent/skills/_evals/`에 두고, 스킬 유/무 비교 실행 후 `validate_page.py`와 체크리스트(섹션 누락, 추측된 담당자, 출처 없는 수치, 외부 리소스)로 평가한다. 통과 후 `improve_description.py`로 트리거 문구를 다듬는다.

### 5.2 하네스 정의 (CDK `CfnResource`, 타입 `AWS::BedrockAgentCore::Harness`)

| 속성 | 값 |
|---|---|
| HarnessName | `transmeet_meeting_page` |
| Model | `BedrockModelConfig { ModelId: us.anthropic.claude-opus-5-5, MaxTokens: 32000 }` |
| SystemPrompt | `agent/harness/system_prompt.md` 내용 (synth 시 파일 읽기) |
| Tools | `agentcore_code_interpreter` (name `code_interpreter`), `agentcore_gateway` (name `web_search`, 아래 Gateway ARN, `outboundAuth.awsIam`), `inline_function` `publish_page` |
| Skills | `s3://<skills-bucket>/skills/meeting-minutes-page/`, `.../meeting-research/`, `.../meeting-insights/`, `.../meeting-diagrams/` |
| Memory | `Disabled: {}` |
| MaxIterations | 80 |
| TimeoutSeconds | 900 |
| Environment | 기본(PUBLIC 네트워크), idle 600초, maxLifetime 1800초 |

`publish_page` 입력 스키마:

```json
{
  "type": "object",
  "properties": {
    "title": { "type": "string", "description": "페이지 제목(미팅 제목)" },
    "html": { "type": "string", "description": "완성된 단일 HTML 문서 전체" },
    "share_summary": { "type": "string", "description": "공유 시 함께 보낼 2문장 요약" }
  },
  "required": ["title", "html"]
}
```

시스템 프롬프트 요지: 역할(미팅록 페이지 제작자), 입력 형식, 반드시 네 스킬을 읽고 따를 것, 작업 순서, 한국어 본문·원문 병기, JS·외부 리소스 금지, `validate_page.py` 통과 후에만 `publish_page`를 **정확히 한 번** 호출, 호출 결과 URL을 받은 뒤 두 문장 이내 한국어 마무리. 진행 중 사용자에게 보이는 짧은 상태 문장을 단계마다 한 줄씩 출력(UI 로그용).

### 5.3 인프라 추가 (`infra/lib/transmeet-stack.ts`)

| 리소스 | 세부 |
|---|---|
| `AgentSkillsBucket` | 비공개 S3. `skills/` 프리픽스에 스킬 업로드 |
| `MeetingPagesBucket` | 비공개 S3. 기존 OAI로 CloudFront 읽기 허용. 객체 키 `p/<meetingId>/<pageId>.html` |
| CloudFront behavior `/p/*` | pages 버킷 오리진. `CACHING_DISABLED`(공유 해제 즉시 반영), HTTPS only, GET/HEAD |
| `WebSearchGatewayRole` | 신뢰: `bedrock-agentcore.amazonaws.com`. `bedrock-agentcore:InvokeGateway`(gateway/*), `bedrock-agentcore:InvokeWebSearch`(`arn:aws:bedrock-agentcore:us-east-1:aws:tool/web-search.v1`) |
| `WebSearchGateway` | `CfnGateway`: MCP, `AWS_IAM`, 위 역할 |
| `WebSearchTarget` | Gateway target, 커넥터 `web-search` v`1.2.0`, `GATEWAY_IAM_ROLE`. CDK L1이 `connector`를 지원하지 않으면 `CfnResource`(`AWS::BedrockAgentCore::GatewayTarget`) |
| `HarnessExecutionRole` | 신뢰: `bedrock-agentcore.amazonaws.com`. 문서의 샘플 실행 역할 정책(Bedrock invoke — Opus 5.5 inference profile + foundation-model, ECR Public 토큰, STS bearer, Logs, X-Ray, CloudWatch 메트릭, Workload Identity, Code Interpreter `aws:code-interpreter/*`) + `InvokeGateway`(해당 gateway ARN) + skills 버킷 `s3:GetObject/ListBucket` |
| `MeetingPageHarness` | 5.2의 `CfnResource`. Gateway·역할·버킷에 `addDependency` |
| `wsTaskRole` 추가 권한 | `bedrock-agentcore:InvokeHarness`, `bedrock-agentcore:InvokeAgentRuntime`(harness ARN), pages 버킷 `s3:PutObject/DeleteObject` |
| ECS 컨테이너 env | `HARNESS_ARN`, `PAGES_BUCKET`, `PAGES_PUBLIC_BASE_URL=https://transmeet.hi-yoo.com/p` |
| Outputs | `HarnessArn`, `SkillsBucketName`, `PagesBucketName` |

`deploy.sh`에 `skills` 커맨드 추가: `aws s3 sync agent/skills/ s3://<SkillsBucket>/skills/ --delete --exclude "_evals/*"`. `all`은 `infra → skills → frontend` 순. 백엔드 이미지는 기존대로 수동 ECR 푸시 + `force-new-deployment` (deploy.sh 범위 밖, README에 명시).

### 5.4 백엔드 (`backend/app/`)

**의존성**: `aioboto3` 13.2.0 → **15.5.0** (botocore 1.43.1xx, `bedrock-agentcore` `InvokeHarness` 포함). `amazon-transcribe`는 botocore 비의존이라 영향 없음.

**`page_agent.py`** (신규, `ws_handler.py`와 분리):

- `build_meeting_payload(meeting_item) -> dict`: DynamoDB 아이템에서 제목, 시작/종료, 화자 목록, 메시지(`timestamp`, `speaker`, `originalText`, `translatedText`, `detectedLanguage`)를 추출. 10초 이내 동일 화자 연속 발언 병합(프런트 `mergeLoadedMessages`와 동일 규칙).
- `build_user_message(payload) -> str`: 메타데이터 + JSON 트랜스크립트 + "네 스킬을 따르고 마지막에 publish_page 호출" 지시.
- `run_page_generation(ws, meeting_id, *, inline_messages=None)`: 
  1. `page_stream status "미팅 기록 불러오는 중"` 전송, 메시지 로드(없으면 `error`).
  2. `runtimeSessionId = str(uuid4())` (생성마다 새 세션).
  3. `invoke_harness(... tools=[publish_page 스키마])` 스트림 소비. `contentBlockDelta.text` → `delta`, `contentBlockStart.toolUse` → `status "{도구}: {요약}"`(web_search는 쿼리, code_interpreter는 "검증 실행", publish_page는 "페이지 발행 중"), `metadata.usage` → 로그.
  4. 마지막 `messageStop.stopReason == "tool_use"`이고 `publish_page` 핸드오프가 잡혔으면 `handle_publish(...)` 실행 후 같은 세션으로 `toolResult` 전송, 스트림을 다시 소비(3번 반복). 핸드오프 없는 `tool_use`/`max_iterations_exceeded`/`timeout_exceeded`/에러 → `error`.
  5. `end_turn` → `page_stream done {page: {url, pageId, generatedAt, title, shareSummary}}`.
- `handle_publish(meeting_id, args) -> dict`: `html` 검증(문자열, `<!doctype html` 또는 `<html` 포함, 1.5 MB 이하, `<script` 미포함, `on*=` 이벤트 속성 미포함, `http://`·`https://`가 `href`와 `xmlns`(SVG 네임스페이스) 외 위치(`src`, `@import`, `url(`)에 없음). 실패 시 `status: "error"` toolResult로 사유 반환(에이전트가 수정 후 재호출). 성공 시 `pageId = secrets.token_urlsafe(16)`, S3 `put_object(ContentType="text/html; charset=utf-8", CacheControl="no-cache")`, DynamoDB `SET page = {pageId, url, generatedAt, title, shareSummary}`. 이전 `page.pageId`가 있으면 그 객체 삭제. `{url}` 반환.
- `unpublish_page(meeting_id)`: S3 객체 삭제 + `REMOVE page`.

**`ws_handler.py`**: `action == "generate_page"` → `asyncio.create_task(run_page_generation(...))`, `action == "unpublish_page"` → 삭제 후 `page_stream {phase: "unpublished"}`. 동시 생성 방지: 연결 상태에 `page_task` 보관, 진행 중이면 `error "이미 생성 중"`. 생성 이벤트는 요청한 소켓이 아니라 **그 미팅을 보고 있는 현재 연결 전부**에 보낸다(생성 중 소켓이 바뀌어도 결과를 받는다). `action == "page_status"` → 돌고 있으면 text 없는 `status`, 끝났으면 기억해 둔 마지막 `done`/`error`(30분) 재전송, 기록이 없으면 `error "생성이 중단되었습니다"`. 프런트는 재접속 시와 20초 폴링에서 새 페이지가 없을 때 이를 보낸다.

**`config.py`**: `HARNESS_ARN`, `PAGES_BUCKET`, `PAGES_PUBLIC_BASE_URL`, `PAGE_MAX_BYTES=1_500_000`.

**`aws_clients.py`**: `agentcore_client()` (`bedrock-agentcore`), `s3_client()` 컨텍스트 매니저 추가.

### 5.5 프런트엔드 (`src/`)

- `types/meeting.ts`: `Meeting.page?: { pageId: string; url: string; generatedAt: string; title?: string; shareSummary?: string }`. `lib/api.ts`의 `ApiMeeting.page` 매핑 (GET `/meetings/{id}`는 아이템 전체를 반환하므로 Lambda 수정 불필요).
- `lib/websocket.ts`: `page_stream` 메시지 타입(`phase: status | delta | done | error | unpublished`).
- **`components/MeetingPagePanel.tsx`** (신규):
  - 상태: `idle`(안내 + "미팅 페이지 만들기" 버튼, 메시지 없으면 비활성) / `generating`(상태 로그 리스트, 경과 시간, 중단 불가 안내) / `ready`(`<iframe sandbox="" src={url}>` 미리보기, 버튼: **공유**, 새 탭, 재생성, 공유 해제) / `error`(사유 + 재시도).
  - 공유: `navigator.share` 가능하면 `{title, text: shareSummary, url}`, 아니면 클립보드 복사 + 2초 토스트(기존 복사 UX와 동일).
  - 재생성 확인 다이얼로그: "기존 링크는 더 이상 열리지 않습니다".
- `app/page.tsx`: `activeTab`에 `'page'` 추가. 데스크톱 우측 패널 상단에 `요약 | 미팅 페이지` 세그먼트 토글(기존 `SummaryPanel`과 교대 표시, 패널 리사이즈 공유). `page_stream` 핸들러로 상태 전이. `handleGeneratePage`는 `summarize`와 동일하게 녹음 중이면 인라인 메시지를 함께 전송.
- `MobileTabBar.tsx`: 4번째 탭 `페이지`. 생성 완료 시 비활성 탭에 배지.

### 5.6 데이터 모델 (DynamoDB `transmeet-meetings`)

```
page: {
  pageId: S,          // secrets.token_urlsafe(16)
  url: S,             // https://transmeet.hi-yoo.com/p/<meetingId>/<pageId>.html
  generatedAt: S,     // ISO-8601
  title: S,
  shareSummary: S
}
```

## 6. 오류 처리

| 상황 | 처리 |
|---|---|
| 메시지 0개 | `page_stream error "기록이 없습니다"` 즉시 반환 |
| 하네스 `max_iterations_exceeded` / `timeout_exceeded` | `error` + 사유 표시. 부분 발행 없음 |
| `publish_page` 검증 실패 | `toolResult status=error`로 사유 반환. 최대 2회까지 재호출 허용, 이후 `error` |
| `publish_page` 미호출로 `end_turn` | `error "에이전트가 페이지를 발행하지 않았습니다"` |
| 스킬 fetch 실패(S3 권한·경로) | 하네스가 invocation 실패 → `error` + 로그에 원문 메시지 |
| WebSocket 끊김 | 백엔드 작업은 끝까지 진행해 DynamoDB에 저장. 재접속 후 미팅 재로드 시 `page` 표시 |
| 동시 생성 요청 | 두 번째 요청은 `error "이미 생성 중"` |
| 공유 해제 중 S3 삭제 실패 | DynamoDB는 유지하고 `error` 반환(링크가 살아 있다고 거짓 안내하지 않음) |

## 7. 보안

- 프런트가 보내는 것은 `meetingId`(와 녹음 중 인라인 메시지)만. `skills`, `model`, `tools`는 백엔드 상수로 고정하고 호출자 입력을 하네스에 전달하지 않는다.
- 생성 HTML은 백엔드가 JS·외부 리소스를 검사한 뒤 발행. 앱 내 미리보기는 `sandbox=""` iframe.
- 페이지 버킷은 비공개, CloudFront OAI로만 읽기. 목록 조회 불가. ID는 128비트 난수.
- 하네스 실행 역할은 S3 쓰기 권한 없음. Bedrock 호출은 Opus 5.5 inference profile로 한정.

## 8. 테스트

- **스킬**: skill-creator 평가 루프(샘플 3개, with/without, `validate_page.py` + 체크리스트). 결과물은 `agent/skills/_evals/` 워크스페이스(커밋 제외).
- **백엔드 단위 테스트** (`backend/tests/`, pytest, 신규):
  - `build_meeting_payload` 병합 규칙, `build_user_message` 필수 지시 포함.
  - 스트림 이벤트 파서: 가짜 이벤트 시퀀스로 `delta/status` 매핑, `publish_page` 핸드오프 캡처(여러 delta 조각 조립), `end_turn` 처리.
  - `handle_publish` 검증 규칙(스크립트 포함, 외부 src, 크기 초과 → error toolResult), 성공 시 S3/DynamoDB 호출 인자(가짜 클라이언트 주입).
- **프런트**: `npm run lint`, `npm run build`. 상태 전이는 수동 확인(데모 미팅으로 generating → ready → share).
- **통합**: `agent/scripts/invoke_local.py` — 로컬 자격증명으로 하네스를 직접 호출해 샘플 미팅으로 페이지 생성, URL 접근 확인. 배포 후 1회 실행.

## 9. 비용·시간 가늠

Opus 5.5 기준 미팅 1건(발언 100~200개)에 입력 150~400K 토큰(반복 포함), 출력 20~40K 토큰 수준으로 예상한다. 생성 시간 2~5분. Web Search 호출은 스킬에서 최대 12회로 제한한다. 수치는 첫 배포 후 `metadata.usage` 로그로 보정한다.

## 10. 구현 순서 (요약)

1. 스킬 4개 제작 + 로컬 평가 (skill-creator)
2. 인프라: 버킷·Gateway·역할·Harness·behavior·권한·deploy.sh
3. 백엔드: 의존성 업그레이드, `page_agent.py` + 테스트, `ws_handler` 액션
4. 프런트: 타입·WS 메시지·`MeetingPagePanel`·탭
5. 배포(infra → skills → 백엔드 이미지 → frontend) 및 통합 확인

## 11. 배포 중 확인된 사실 (2026-10-08)

- `global.anthropic.claude-opus-5-5` 프로필은 하네스 내부 `ConverseStream`에서 `ServiceUnavailableException`을 반복 반환했다(4회 중 3회, maxTokens 8000에서도 재현). 직접 `bedrock-runtime` 호출은 정상. `us.anthropic.claude-opus-5-5`는 하네스에서 안정적으로 동작해 모델 ID를 `us.` 프로필로 확정했다.
- `InvokeHarness` 응답 스트림은 긴 도구 실행 구간에 수십 초 이상 이벤트가 없을 수 있어 백엔드 agentcore 클라이언트의 읽기 타임아웃을 900초로 두고 재시도를 끈다.
- 하네스 안에서 Bedrock 이 `us.` 프로필에도 간헐적으로 `ServiceUnavailableException`을 반환한다(2026-10-08 05:44Z, 첫 모델 호출에서 하네스 내부 재시도 2회 소진 후 27초 만에 실패). 백엔드는 발행 전 일시적 오류(ServiceUnavailable/Throttling/ModelNotReady/InternalServer/modelStreamError)에 한해 새 `runtimeSessionId`로 처음부터 최대 `PAGE_AGENT_INVOKE_RETRIES`(2)회 다시 시도한다(대기 5초, 15초). 발행 뒤의 실패는 기존처럼 `done`.
- 같은 사고에서 브라우저 소켓이 실패 3초 전에 바뀌어 `error` 이벤트가 닫힌 소켓으로 나갔고, 프런트는 API 폴링으로 "새 페이지" 만 확인해 16분 타임아웃까지 "생성 중" 에 머물렀다. 위 브로드캐스트 + `page_status` 가 이를 막는다.
- 같은 날 18:55Z~ 는 `us.`/`global.` 두 프로필 모두, 2K 토큰짜리 요청까지 Opus 5.5 가 100% `ServiceUnavailableException`(모델 단위 장애). 다른 Claude 모델(Opus 5, Sonnet 5.5 등)은 40K 토큰 요청도 정상. 이를 위해 모델만 다른 **대체 하네스** `transmeet_meeting_page_fallback`(`us.anthropic.claude-opus-5`, 프롬프트·도구·스킬 동일)을 두고, 백엔드는 기본 하네스의 재시도가 모두 일시 오류로 끝나면 대체 하네스로 넘어가 같은 재시도 예산으로 다시 시작한다(`HARNESS_FALLBACK_ARN`, 상태 메시지 "기본 모델이 응답하지 않아 대체 모델로 생성합니다"). 대체 모델로 만든 페이지는 `transmeet:model` meta 에 그 모델 ID가 기록된다.
