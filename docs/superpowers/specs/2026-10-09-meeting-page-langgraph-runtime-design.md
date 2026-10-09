# 미팅 페이지 에이전트 v2 (LangGraph + AgentCore Runtime) 설계

작성 2026-10-09. 전작 `2026-10-08-meeting-page-agent-design.md`(AgentCore Harness)의 **대체** 설계다. 페이지 내용·스킬·프런트 UI·공유 URL 형식은 그대로 두고, 에이전트의 실행 방식과 백엔드 연결만 바꾼다.

## 1. 왜 바꾸는가

하네스 운용에서 확인된 문제(전작 §11):

- 하네스 내부 모델 호출·도구 구성이 블랙박스다. `tools=` 가 도구 목록을 대체하는 사실, 하네스 내부 읽기 타임아웃 등을 운영 중에 발견했다.
- 한 ReAct 루프가 80회까지 돌면서 **매 반복에 트랜스크립트를 다시 보낸다.** 329개 발언 미팅이 입력 1.04M 토큰, 19분이 걸렸다.
- 샌드박스(code interpreter)에서 만든 HTML 을 `publish_page` 인자로 **모델이 다시 출력**해야 했다 (출력 20K 토큰, 7분 이상).
- 모델 장애(Opus 5.5 ServiceUnavailable) 대응을 위해 하네스를 두 개 두어야 했다.

v2 는 에이전트를 우리가 직접 작성해 AgentCore Runtime(컨테이너)에 올린다. 그 결과:

- 단계별 그래프로 **트랜스크립트를 단계 수만큼만** 보낸다(5회 안팎).
- 도구(셸·파일·검색·발행)가 **Runtime 세션의 microVM 안에서** 돈다. 발행 도구는 로컬 파일을 읽어 올리므로 HTML 재출력이 없다.
- 모델 ID 는 호출 페이로드로 받는다. 대체 모델은 설정값 하나다.

## 2. 사용자가 확정한 결정 (2026-10-09)

| 항목 | 결정 |
|---|---|
| 프레임워크 | **LangGraph** (`langgraph` 최신, 1.2.x) + `langchain-aws` `ChatBedrockConverse` |
| 코드 실행 | **Runtime 컨테이너 안에서 직접** (code interpreter 사용 안 함) |
| 발행 주체 | **에이전트가 직접** S3 업로드 + DynamoDB 기록. 백엔드는 이벤트 중계·재시도·공유 해제만 |
| 하네스 | 제거 (기본·대체 모두). Gateway(Web Search)는 유지해 MCP 로 직접 접속 |
| 스킬 | 컨테이너 이미지에 포함. S3 스킬 버킷은 더 쓰지 않음 |

## 3. 아키텍처

```
브라우저 ──WS generate_page──▶ ECS FastAPI (ws_handler → page_agent)
                                 │ InvokeAgentRuntime (payload JSON, 응답 SSE 스트림, 최대 60분)
                                 ▼
                     AgentCore Runtime "transmeet_meeting_page"  (ARM64 컨테이너, 세션 = microVM)
                     ├─ BedrockAgentCoreApp (/invocations, /ping)  ← bedrock-agentcore SDK
                     ├─ LangGraph StateGraph
                     │    prepare ─▶ outline ─▶ ┬ research  (web_search ≤12)
                     │                          └ diagrams  (코드: timeline_svg / 모델: flow·relations)
                     │                        ─▶ insights (리서치 판정을 입력으로 받음)
                     │                        ─▶ assemble ─▶ validate ─▶ publish
                     │                                ▲          │ 실패(≤2회)
                     │                                └──────────┘
                     ├─ 도구: load_skill / bash / read_file / write_file / edit_file / web_search / (publish 는 노드)
                     ├─ 모델: ChatBedrockConverse(model_id = payload.meeting.modelId, max_tokens 64000)
                     └─ 발행: S3 pages 버킷 put → DynamoDB meeting.page SET (실행 역할 권한)
                                 │ SSE: {"phase": status|delta|done|error, ...}
                                 ▼
                      ECS FastAPI: page_stream 으로 감싸 그 미팅의 현재 연결 전부에 브로드캐스트
브라우저 ◀── page_stream status/delta/done/error ──
사용자 ──▶ https://transmeet.hi-yoo.com/p/<meetingId>/<pageId>.html  (변경 없음)
```

### 3.1 왜 이 구조인가

- **단계 그래프**: 각 단계가 트랜스크립트를 한 번 받고 짧은 도구 루프를 돈다. 리서치와 다이어그램은 서로 독립이라 **병렬**로 돌리고, 인사이트는 리서치 판정("상이함" 항목을 리스크로)을 입력으로 받으므로 그 뒤에 돈다. 조립 단계만 길다.
- **프롬프트 캐시**: 도구 루프의 매 반복은 같은 접두(시스템 프롬프트 + 트랜스크립트 + 단계 입력)를 다시 보낸다. 두 메시지 끝에 Bedrock `cachePoint` 를 두어 반복 호출의 접두를 캐시에서 읽는다(입력 단가 약 1/10). 확인: 41K 토큰 접두가 두 번째 호출에서 전부 `cache_read`.
- **로컬 도구**: Runtime 세션은 세션마다 격리된 microVM 이다. `bash` 로 스킬 스크립트(`validate_page.py`, `timeline_svg.py`, `flow_svg.py`)를 바로 실행하고, 결과 파일은 작업 디렉터리에 남아 다음 단계가 읽는다.
- **HTML 재출력 제거**: 조립 단계는 `write_file` 로 `page.html` 을 쓰고 검증 실패 시 `edit_file`(문자열 치환)로 고친다. 발행 노드는 파일을 읽어 올린다. 모델이 HTML 을 두 번 출력하는 일이 없다.
- **스트리밍 응답**: Runtime 스트리밍 연결 상한은 60분(동기 요청은 15분). 생성은 10~20분이므로 스트리밍으로 받는다. 이벤트 형식은 백엔드가 이미 쓰는 `phase` 체계를 그대로 쓴다.
- **모델 선택을 페이로드로**: 백엔드의 재시도·대체 모델 로직(검증 완료)을 유지한다. 하네스 ARN 두 개가 모델 ID 두 개로 바뀐다.

## 4. 에이전트 (`agent/runtime/`)

Python 3.13, 패키지 `agent/runtime/`. 의존성은 `agent/requirements.txt` 에 고정: `langgraph==1.2.14`, `langchain-core==1.6.9`, `langchain-aws==1.8.1`, `mcp==2.3.0`, `bedrock-agentcore==1.24.1`, `boto3==1.43.110`, `httpx`. 로컬 개발은 `agent/.venv`.

### 4.1 입력·출력 계약

**입력 페이로드** (백엔드 `build_meeting_payload` 그대로):
```json
{"meeting": {"meetingId","title","startedAt","endedAt","speakers","languages","generatedAt","modelId"},
 "messages": [{"timestamp","speaker","original","translation","detectedLanguage"}]}
```

**출력 SSE 이벤트** (한 줄 JSON, `data: ...`):

| phase | 필드 | 의미 |
|---|---|---|
| `status` | `text` | 단계 전환·도구 시작 한 줄 (예: "리서치 대상 6개를 검색합니다", "웹 검색: AgentCore Runtime quotas") |
| `delta` | `text` | 모델 텍스트 조각(조립 단계의 상태 문장 등) |
| `done` | `page` {pageId,url,generatedAt,title,shareSummary}, `usage` | 발행 완료 |
| `error` | `error`, `transient`(bool), `stage` | 실패. `transient=true` 는 Bedrock 일시 오류(ServiceUnavailable/Throttling/ReadTimeout) |

`done`/`error` 는 반드시 마지막 한 번 나온다. 백엔드는 둘 중 하나를 받지 못하고 스트림이 끝나면 `error` 로 본다.

### 4.2 상태와 그래프 (`graph.py`)

```python
class PageState(TypedDict):
    meeting: dict; messages: list[dict]; work_dir: str; model_id: str
    outline: dict            # topics[], research_targets[], diagram_plan[]
    research_html: str; insights_html: str; diagram_files: list[str]
    page_path: str; validation_errors: list[str]; assemble_attempts: int
    published: dict | None; usage: dict
```

노드:

| 노드 | 모델 | 도구 | 산출 |
|---|---|---|---|
| `prepare` | 없음 | — | `work_dir` 생성, `transcript.json`·`meeting.json` 저장, 스킬 디렉터리 경로 확인 |
| `outline` | 1회 호출(구조화 출력) | — | 주제 분할(시각·주제명·요약), 리서치 대상(≤10), 다이어그램 계획(타임라인 + 선택적 흐름/관계도). `outline.json` 저장 |
| `research` | 도구 루프 ≤20 | `load_skill`, `web_search`, `read_file`, `write_file` | `research.html` (meeting-research 출력 포맷). 대상 0개면 모델 호출 없이 고정 문구 |
| `insights` | 도구 루프 ≤6 | `load_skill`, `read_file`, `write_file` | `insights.html` |
| `diagrams` | 코드(타임라인) + 도구 루프 ≤12(flow/relations 가 계획에 있을 때만) | `load_skill`, `bash`, `read_file`, `write_file` | `diagrams/timeline.svg` + `diagrams/*.svg` + `diagrams/manifest.json` (파일·캡션·주제 번호) |
| `assemble` | 도구 루프 ≤25 | `load_skill`, `read_file`, `write_file`, `edit_file`, `bash` | `page.html` (코드가 템플릿에서 생성·헤더·references·insights·타임라인 채움 → 모델이 내용 자리표시자 6개를 `edit_file` 로 채움 → `validate_page.py` 실행) + `page.meta.json` |
| `validate` | 없음 | — | 백엔드와 같은 엄격 검증기(`validator.py`) 실행. 오류면 `validation_errors` 를 채워 `assemble` 로 되돌림(최대 2회), 아니면 `publish` |
| `publish` | 없음 | — | `page.html` 읽어 S3 put, DynamoDB `SET page`, 이전 pageId 객체 삭제. `published` 설정 |

엣지: `prepare → outline → [research, diagrams] (병렬) → insights → assemble → validate → (publish | assemble | fail) → END`. 병렬 분기는 LangGraph 의 fan-out 엣지로, `insights` 는 두 노드가 모두 끝난 뒤 실행된다(fan-in).

코드가 하는 일(모델이 하지 않는 일): 헤더·푸터 값(`derive_meta`: 제목·일시·소요·참석자·언어·생성 시각·조회일), 주제 타임라인 SVG(`timeline_svg.py` 를 outline 의 topics 로 실행), 템플릿에 `references`/`insights`/타임라인 삽입(`_seed_page`), `{{DIAGRAM:파일.svg}}` 마커를 `<figure>`+캡션으로 교체(`_inline_diagrams`), 검증, 발행. 모델이 채우는 자리표시자는 `TLDR_ITEMS, DECISIONS, ACTION_ROWS, TOPIC_BLOCKS, OPEN_QUESTIONS, GLOSSARY_ROWS` 여섯 개이며 `edit_file` 로 하나씩 바꾼다.

각 도구 루프 노드는 공통 함수 `run_stage(state, *, name, system_prompt, user_prompt, tools, max_iterations)` 로 구현한다. 내부는 `ChatBedrockConverse.bind_tools(tools)` → 모델 호출 → `tool_calls` 실행 → 반복. 반복 상한에 걸리면 그 단계는 "확인 불가"류 기본 산출물로 끝내고 다음 단계로 간다(`assemble` 만 예외: 상한이면 `error`). 모델 텍스트는 `delta` 로, 도구 호출 시작은 `status` 로 흘려 보낸다(`langgraph.config.get_stream_writer()` 로 커스텀 이벤트 발행).

### 4.3 도구 (`tools.py`, `web_search.py`)

모두 `work_dir` 안으로 경로를 제한한다(`..`·절대 경로 거절). 스킬 디렉터리는 읽기 전용으로 `work_dir/skills` 에 심볼릭 링크.

- `load_skill(name)`: `skills/<name>/SKILL.md` 본문과 `references/`, `scripts/`, `assets/` 파일 목록을 반환. 이름은 4개 중 하나만.
- `read_file(path)`, `write_file(path, content)`, `edit_file(path, old, new)`(정확히 1회 일치해야 성공).
- `bash(command, timeout_s=120)`: `work_dir` 에서 `subprocess.run`. 출력 20KB 로 잘라 반환. 네트워크 접근 금지는 프롬프트로만 막지 않고, 검색은 `web_search` 를 쓰라고 시스템 프롬프트에 명시한다(Runtime PUBLIC 네트워크라 curl 은 가능하지만 검색 엔진은 차단된다 — 전작에서 확인).
- `web_search(query, max_results=5, include_domains=[])`: AgentCore Gateway(`transmeet-web-search`)에 MCP streamable HTTP 로 접속, 도구 `web-search-tool___WebSearch` 호출. 인증은 SigV4(`httpx.Auth` 구현, 서비스 `bedrock-agentcore`)를 붙인 `httpx.AsyncClient` 를 `mcp.client.streamable_http.streamable_http_client(url, http_client=...)` 에 넘긴다(mcp 2.x 시그니처). 세션당 MCP 연결 1개를 재사용. 호출 수는 노드 단위로 12회 상한.

### 4.4 발행·검증 (`validator.py`, `publish.py`)

- `validator.py`: 백엔드 `page_agent.py` 의 `_HtmlAudit`/`_validate`/`validate_html` 과 `REQUIRED_SECTIONS`/`REQUIRED_META`/CSS 검사 **이동**(복사 아님 — 백엔드에서 삭제). 테스트 `backend/tests/test_page_agent_publish.py` 의 검증 부분과 `validator_corpus.py` 는 `agent/tests/` 로 이동.
- `publish.py`: 백엔드 `publish_page`/`unpublish_page` 의 S3·DynamoDB 로직을 boto3(동기, `asyncio.to_thread`)로 이식. 인자는 `html_path` 가 아니라 **읽어 둔 HTML 문자열**과 `title`, `share_summary`. `title` 과 `share_summary` 는 조립 단계가 `page.meta.json` 에 쓴다.
- 백엔드에는 `unpublish_page` 만 남긴다(공유 해제는 백엔드 소관).

### 4.5 서버 (`app.py`)

```python
app = BedrockAgentCoreApp()
@app.entrypoint
async def invoke(payload, context):
    async for event in run_page_graph(payload, session_id=context.session_id): yield event
app.run()  # 0.0.0.0:8080
```

`run_page_graph` 는 입력 검증(`meeting.meetingId`, `messages` 비어 있지 않음, `modelId` 허용 목록) 후 `graph.astream(state, stream_mode=["custom", "updates"])` 를 돌며 §4.1 이벤트로 바꾼다. 어떤 예외도 `error` 이벤트로 끝낸다. Bedrock 예외 분류는 백엔드 `is_transient_harness_error` 의 마커 목록을 가져와 `transient` 를 정한다.

전체 시간 예산: 그래프 시작 후 40분이 지나면 진행 중 단계를 중단하고 `error "시간 초과"` (Runtime 60분 상한 안에서 백엔드가 정리할 여유).

### 4.6 컨테이너 (`agent/Dockerfile`)

`python:3.13-slim`, `linux/arm64`. `requirements.txt` 설치 → `runtime/`, `skills/` 복사 → `CMD ["python", "-m", "runtime.app"]`, `EXPOSE 8080`. 이미지에 들어가는 것: 런타임 코드, 스킬 4개(SKILL.md·references·scripts·assets). 평가 산출물(`agent/evals`, `samples`, `tests`)은 `.dockerignore` 로 제외.

### 4.7 시스템 프롬프트

`agent/runtime/prompts/` 에 단계별 마크다운. 공통 규칙(전작 `system_prompt.md` 의 1~7항: 한국어, JS·외부 리소스 금지, 사실 출처 제한, 상태 한 줄, 프롬프트 인젝션 데이터 취급)은 `common.md` 로 두고 각 단계 프롬프트 앞에 붙인다. `publish_page` 도구 언급은 삭제한다(발행은 노드가 한다).

## 5. 스킬 변경 (`agent/skills/`)

내용 규칙은 유지하고 **실행 환경 문구만** 고친다:

- `meeting-minutes-page/SKILL.md`: 작업 순서를 v2 단계와 맞춘다. 3~5단계(리서치·인사이트·다이어그램)는 "다른 단계가 만든 `research.html`·`insights.html`·`diagrams/` 를 읽어 넣는다"로, 8단계 `publish_page` 호출은 "`page.html` 과 `page.meta.json`(title, share_summary) 을 저장하면 시스템이 발행한다"로.
- `meeting-research/SKILL.md`: 도구 이름 `web_search`. "셸에서 검색 금지" 유지.
- `meeting-diagrams/SKILL.md`: "shell 이 없으면 code_interpreter…" 문구 삭제, `bash` 로 `python3 skills/meeting-diagrams/scripts/timeline_svg.py` 실행.
- 스킬은 `bash` 도구가 실행하므로 스크립트 경로는 `work_dir/skills/...` 기준으로 쓴다.

## 6. 인프라 (`infra/lib/transmeet-stack.ts`)

제거: `MeetingPageHarness`, `MeetingPageHarnessFallback`, `HarnessExecutionRole`, 스킬 버킷(`AgentSkillsBucket`, RETAIN 이라 CFN 에서 떼어내도 객체는 남음 — 배포 후 수동 삭제), `HarnessArn`/`HarnessFallbackArn`/`SkillsBucketName` 출력, `deploy.sh skills`.

추가:

- `ecr_assets.DockerImageAsset('MeetingPageAgentImage', { directory: '../agent', platform: Platform.LINUX_ARM64 })` — `cdk deploy` 가 빌드·푸시(부트스트랩 자산 레포). 로컬에 qemu arm64 binfmt 필요(설치됨).
- `RuntimeExecutionRole` (`bedrock-agentcore.amazonaws.com` 신뢰): ECR 풀(자산 레포 `GetAuthorizationToken`, `BatchGetImage`, `GetDownloadUrlForLayer`), logs/xray/cloudwatch(기존 하네스 역할과 동일 문장), `GetWorkloadAccessToken*`(`workload-identity/transmeet_meeting_page-*`), Bedrock `InvokeModel*`(두 모델 inference-profile + foundation-model, 기존 범위 유지), `bedrock-agentcore:InvokeGateway`(Web Search Gateway ARN), pages 버킷 `PutObject`/`DeleteObject`, meetings 테이블 `UpdateItem`.
- `bedrockagentcore.CfnRuntime('MeetingPageRuntime')`: `agentRuntimeName: 'transmeet_meeting_page'`, `agentRuntimeArtifact.containerConfiguration.containerUri = image.imageUri`, `networkConfiguration.networkMode: 'PUBLIC'`, `protocolConfiguration: 'HTTP'`, `roleArn`, `lifecycleConfiguration { idleRuntimeSessionTimeout: 600, maxLifetime: 3600 }`, `environmentVariables { REGION, PAGES_BUCKET, PAGES_PUBLIC_BASE_URL, MEETINGS_TABLE, WEB_SEARCH_GATEWAY_URL, PAGE_MAX_BYTES, ALLOWED_MODEL_IDS }`. `CfnRuntimeEndpoint` 는 만들지 않고 `DEFAULT` 엔드포인트 사용.
- 태스크 역할: `bedrock-agentcore:InvokeAgentRuntime` on runtime ARN + `/*`. pages 버킷 Put 권한은 제거(Delete 는 공유 해제용으로 유지).
- 백엔드 환경: `PAGE_AGENT_RUNTIME_ARN`, `PAGE_AGENT_MODEL_ID`, `PAGE_AGENT_FALLBACK_MODEL_ID`. `HARNESS_*` 삭제.
- 출력: `MeetingPageRuntimeArn`.
- `infra/test/harness-resources.test.sh` → `agent-resources.test.sh`: Runtime(이름·PUBLIC·HTTP·lifecycle·env 키), 실행 역할 모델 범위(와일드카드 금지), 태스크 역할 InvokeAgentRuntime, Gateway/Target/CloudFront 검사 유지, Harness 리소스 **없음** 확인.

## 7. 백엔드 (`backend/app/`)

`page_agent.py` 는 오케스트레이션만 남긴다(약 절반으로 줄어듦):

- 유지: `merge_messages`, `build_meeting_payload`, `check_*`, `PageRequestError`, `page_status_event`/`_remember_result`, `_emitter`, `start_page_generation`, `run_unpublish`(`unpublish_page` 포함), `is_transient_harness_error` → `is_transient_agent_error`(마커 동일).
- 삭제: `_HtmlAudit`/`validate_html`/`publish_page`/`PUBLISH_TOOL_DEF`/`ToolHandoff`/`consume_stream`/`describe_tool_start`/`build_user_message`.
- 변경: `run_page_generation`
  1. 페이로드 생성(`modelId = config.PAGE_AGENT_MODEL_ID`).
  2. `invoke_agent_runtime(agentRuntimeArn, runtimeSessionId=uuid4 두 개 이어 33자 이상, contentType="application/json", accept="text/event-stream", payload=json)`.
  3. `response["response"].iter_lines()` 로 `data: ` 접두 JSON 을 읽어 `page_stream` 으로 중계. `done` 이면 `page` 를 `_remember_result` 하고 종료, `error` 면 `transient` 에 따라 재시도 판단.
  4. 재시도 정책은 현재와 동일: 발행 전 일시 오류 → 새 세션으로 `PAGE_AGENT_INVOKE_RETRIES`(2)회, 소진 시 `PAGE_AGENT_FALLBACK_MODEL_ID` 로 바꿔 다시 2회. 비일시 오류는 즉시 `error`.
  5. 스트림이 `done`/`error` 없이 끝나면 `error "에이전트 응답이 끊어졌습니다"` (일시 오류 취급).
- `config.py`: `PAGE_AGENT_RUNTIME_ARN`, `PAGE_AGENT_MODEL_ID="us.anthropic.claude-opus-5-5"`, `PAGE_AGENT_FALLBACK_MODEL_ID="us.anthropic.claude-opus-5"`. `HARNESS_ARN`/`HARNESS_FALLBACK_ARN`/`PAGE_AGENT_PUBLISH_RETRIES`/`PAGE_MAX_BYTES` 삭제.
- `aws_clients.py`: agentcore 클라이언트 설정 유지(read_timeout 1800 → 2700, 재시도 0).
- `ws_handler.py`: 변경 없음(이벤트 형식 동일).

## 8. 프런트엔드

변경 없음. `PAGE_GEN_TIMEOUT_MS` 31분은 그대로 둔다(그래프 예산 40분보다 짧지만, `page_status` 로 결과를 되찾는 경로가 있다 — 조정은 실측 후).

## 9. 오류 처리

| 상황 | 처리 |
|---|---|
| 페이로드 검증 실패(메시지 0개, 허용되지 않은 modelId) | 즉시 `error`(비일시) |
| Bedrock ServiceUnavailable/Throttling/ReadTimeout | 단계 안에서 2회 백오프 재시도 → 그래도 실패면 `error transient=true` → 백엔드가 새 세션·대체 모델로 재시도 |
| research/insights/diagrams 반복 상한 | 기본 산출물("확인 불가"/빈 다이어그램)로 진행, `status` 로 알림 |
| assemble 반복 상한, validate 3회 실패 | `error "페이지 조립에 실패했습니다: <첫 오류 3개>"` |
| S3/DynamoDB 실패 | 업로드 롤백(best-effort) 후 `error`(비일시) |
| 그래프 40분 초과 | `error "시간 초과"` |
| 백엔드 스트림 끊김 | `error` 일시 취급 → 재시도. 발행이 이미 끝났다면(DynamoDB 에 새 page) 재시도 전 `page_status`/DynamoDB 확인으로 중복 생성 방지: 재시도 직전 `_load_meeting` 으로 `page.generatedAt > 시작시각` 이면 `done` 으로 마무리 |
| 동시 생성 | 기존과 동일(`이미 생성 중`) |

## 10. 보안

- 프런트 입력은 `meetingId`(와 인라인 메시지)만. 모델 ID·도구는 백엔드/Runtime 상수.
- 도구 경로는 `work_dir` 로 제한. `bash` 는 세션 microVM 안에서만 돈다. Runtime 실행 역할의 쓰기 권한은 pages 버킷(Put/Delete)과 meetings 테이블(UpdateItem)로 한정.
- 발행 전 엄격 검증기(JS·`on*`·외부 리소스·필수 섹션·메타·크기)는 그대로 Runtime 안에서 강제된다. CloudFront CSP sandbox 는 유지.
- Runtime 인바운드 인증은 IAM(기본). 호출자는 ECS 태스크 역할만.
- Gateway 는 IAM SigV4. 실행 역할에만 `InvokeGateway`.

## 11. 테스트

- **agent 단위** (`agent/tests/`, venv pytest): 경로 제한(`..` 거절), `edit_file` 1회 일치 규칙, `load_skill` 허용 목록, 검증기 코퍼스(백엔드에서 이동), `publish` S3/DDB 호출 인자·롤백·이전 pageId 삭제(가짜 클라이언트), 그래프 라우팅(가짜 모델: outline → 병렬 3 → assemble → validate 실패 1회 → 재조립 → publish), SSE 이벤트 변환(`done`/`error` 마지막 보장, transient 분류), 전체 시간 예산.
- **backend 단위**: 재시도·대체 모델·`page_status` 테스트를 SSE 라인 가짜 스트림으로 바꿔 유지. 검증기·발행 테스트는 agent 로 이동.
- **infra**: `agent-resources.test.sh`.
- **통합**: `agent/scripts/invoke_local.py --direct --sample ...` 는 그래프를 로컬 프로세스에서 실행(로컬 자격증명, 실제 Bedrock·Gateway·S3), `--runtime` 은 배포된 Runtime 호출. 배포 후 tech-review 샘플과 [SKI] 미팅으로 끝까지 확인(페이지 HTTP 200, 리서치 링크 존재, 다이어그램 수).

## 12. 비용·시간 실측 (2026-10-09)

| 실행 | 시간 | 입력 토큰(캐시 읽기 포함) | 캐시 읽기 | 출력 토큰 | 비고 |
|---|---|---|---|---|---|
| short-1on1 (8 발언) 로컬 직접 | 259s | 237K | — | 12.9K | 캐시 전 |
| short-1on1 로컬 직접 | 300s | 207K | (측정 전) | 11.9K | 캐시 후 |
| tech-review (샘플, 24분 미팅) Runtime v1 | **413s** | 315K | — | 19.5K | 하네스 때 ~15분 |
| tech-review Runtime v2 (스트리밍+캐시) | **365s** | 316K | 120K (38%) | 23.9K | 공식 문서 링크·다이어그램 2개 |
| **[SKI] 실제 미팅 (329 발언) 앱 경로, Runtime v2** | **631s (10.5분)** | 983K | 619K (63%) | 37.2K | 하네스 때 19분 · 1.04M(캐시 없음). 정가 환산 입력 ≈ 426K. 12 주제·29 인용·SVG 3·링크 13 |
| short-1on1 로컬 직접, 페이지 v3 | 300s | 213K | 77K (36%) | 14.0K | analyze 1회, 용어 11개, 요약 4문단 |
| **[SKI] (78 병합 발언) Runtime v4, 페이지 v3** | **500s (8.3분)** | 738K | 417K (57%) | 40.8K | 번역·화자 제거로 입력 -25%. 9 주제·26 인용·SVG 3·링크 16·용어 25(녹취 변형 표기 포함)·요약 5문단. 첫 시도는 Bedrock 스트림이 3분 만에 끊겨 실패 → 일시 오류로 분류해 재시도(v4) |
| [SKI] Runtime v5(용어 사전 CSS 수정), 페이지 v3 최종 | 660s (11분) | 762K | 392K (51%) | 47.0K | 발행 페이지 https://transmeet.hi-yoo.com/p/0df46d5c-fe7a-475e-bdaa-48896fed2c88/e7-XNd6KNemzOw3YFF3enw.html · 9 주제·26 인용·용어 28·링크 17. 같은 입력이라도 리서치 검색 수·조립 반복에 따라 ±2분 |
| **전체 미팅 일괄 생성** (`scripts/invoke_batch.py`, 12 병렬, 113개) | 1시간 46분 · 중앙값 12분/건 | 87.4M (112건 합계) | 50.8M (58%) | 4.2M | 112 성공 · 1 실패([LGE] OSMO cadence: DynamoDB 항목 400 KB 한도 — 발언 1,156개로 항목이 393 KB 라 `page` 속성 UpdateItem 거부). 일시 오류 재시도 2건(InvokeAgentRuntime InternalServerException 1, Bedrock ServiceUnavailable 1) |

관찰:
- 벽시계는 하네스 대비 절반 이하. 입력 토큰은 단계 수가 아니라 **도구 루프 반복 수 × (프롬프트 + 스킬 문서 + 도구 결과)** 로 결정된다. 짧은 미팅에서도 200K 를 넘는 이유다. 캐시 포인트는 시스템·사용자 프롬프트까지만 덮으므로 적중률이 38% 에 머문다. 도구 결과(스킬 본문) 뒤에도 캐시 포인트를 두면 더 오를 것이다(후속).
- Opus 5.5 는 60K 토큰 입력에서 첫 토큰까지 5분을 넘기는 경우가 있다(329 발언 미팅의 outline·research 호출에서 botocore 기본 300초 읽기 타임아웃 발생). 모든 모델 호출을 스트리밍 + 읽기 타임아웃 900초로 바꿨다(v2 이미지).
- Bedrock 의 Opus 5.5 는 `tool_choice` 강제(`tool`/`any`)를 거절한다(ValidationException). outline 은 `Outline` 도구를 auto 로 두고 프롬프트로 호출을 지시하며, 호출이 없으면 한 번 되묻는다.

## 13. 배포 순서

1. 인프라(`./deploy.sh infra`): 이미지 빌드·푸시, Runtime 생성, 하네스·역할 삭제, 태스크 정의 환경 변경(ECS 는 새 태스크 정의로 롤링).
2. 백엔드 이미지 ECR 푸시 + force-new-deployment(기존 절차).
3. 통합 확인(`invoke_local.py --runtime`, 앱에서 [SKI] 생성).
4. 스킬 버킷 수동 비우기·삭제(완료: `transmeet-agent-skills-913524902871` 삭제), 메모리·문서 갱신.

페이지 기록 저장 위치(2026-10-09 변경, §15): 미팅 항목은 발언 전체를 인라인으로 담고 있어 발언이 1,000개를 넘는 미팅은 DynamoDB 항목 한도(400 KB)에 닿는다. 그러면 어떤 UpdateItem 도 `ValidationException: Item size to update has exceeded the maximum allowed size` 로 실패한다([LGE] OSMO cadence 가 그랬다). 그래서 페이지 기록은 미팅 항목의 `page` 속성이 아니라 **별도 테이블 `transmeet-pages`** 에 둔다. 같은 미팅은 새 발언 추가도 곧 같은 한도에 막힌다 — 이것은 앱 저장 구조의 별개 문제다.

운영 주의: 백엔드 이미지를 **먼저** 푸시한 뒤 `cdk deploy` 를 돌린다(태스크 정의 교체 시 새 이미지가 뜸). ECS 롤링 중에는 태스크가 2개여서 진행 중인 생성의 WebSocket 브로드캐스트가 다른 프로세스에 닿지 않고, 종료되는 태스크의 생성은 끊긴다. Runtime 은 세션 단위로 이미지를 고정하므로 배포 시점에 돌던 세션은 이전 이미지로 끝까지 간다.

## 14. 페이지 v3 — 분석 단계·화자 제거·구조 재설계 (2026-10-09, 사용자 피드백 반영)

[SKI] v2 페이지를 본 사용자 피드백: 참석자 구분이 불필요, 페이지가 예쁘지 않고 구조가 없음, 인용은 번역 없이 원문만, 페이지 전에 녹취록 분석(자주 쓰이는 기술 용어를 맥락에 맞게 정확히 표기) 단계가 있으면 좋겠음, 논의 흐름 위에 회의 전체 요약 섹션이 필요. 사용자 확정: 인용은 **가볍게 정제**(필러 제거·`…` 생략·용어 교정), 분석은 **한 번에**(outline 을 대체).

### 14.1 그래프 변경
- `outline` → **`analyze`**. 모델이 트랜스크립트를 한 번 읽고 `Analysis` 도구 호출로 `analysis.json` 을 낸다: `summary`(소제목 그룹 3~4개 × 불릿 — 각 불릿에 `<b>` 핵심 구절 하나·`<mark>` 수치/일정; 2026-10-09 이전에는 서술 3~6 문단; 주제 요지는 서술 문단 유지), `terms`(정식 표기·녹취 변형 표기·이 미팅 맥락에서의 뜻·언급 횟수), `topics`, `research_targets`, `diagram_plan`. 모델 호출 수는 v2 와 같다.
- research·diagrams·insights·assemble 의 사용자 프롬프트에 **용어 표기 블록**(`_terms_block`)을 넣어 모든 단계가 같은 정식 표기를 쓴다. 인용 원문의 음성 인식 오류(예: MSCC → MACC, 블레이크 → 레이크)는 이 표기로 바로잡는다.
- 트랜스크립트 렌더링은 `[HH:MM:SS] 원문` 한 줄. 화자·언어·번역을 넣지 않아 모델 입력이 30% 이상 줄어든다. 헤더에서 참석자·언어 제거.
- 코드가 채우는 것이 늘었다: 목차(`{{TOC}}`: 섹션 9개 + `#topic-N` 주제 목록), 회의 요약(`{{SUMMARY}}`), 용어 사전(`{{GLOSSARY_ROWS}}`: 언급 횟수 순, "용어 | 이 미팅에서의 뜻 | 녹취 표기"), 조립 뒤 헤더 통계 띠(`{{STATS}}`: 주제·결정·액션·미결·참고 자료 개수를 `_stats_html` 이 셈 — "해당 없음" 류 자리표시 항목은 세지 않음). 모델 자리표시자는 `TLDR_ITEMS`, `DECISIONS`, `ACTION_ROWS`, `TOPIC_BLOCKS`, `OPEN_QUESTIONS` 5개.

### 14.2 페이지 구조 (템플릿 v3)
목차(넓은 화면 좌측 고정) · 헤더(제목·일시·소요·생성 + 통계 띠) · 1 한눈에 보기 · **2 회의 요약** · 3 결정 사항(번호 카드) · 4 액션 아이템(상태 배지 `todo`/`doing`/`done`) · 5 논의 흐름(타임라인 + 주제 블록: `topic-head`(번호·제목·시간) → `요지` → `핵심 발언` → `질문과 답` → 다이어그램) · 6 미결 사항 · 7 관련 공식 자료 · 8 인사이트 보고서 · 9 용어 사전 · 푸터. 검증기 필수 섹션에 `summary` 추가(11개). 인용은 `<blockquote><span class="time">[HH:MM]</span>원문</blockquote>` — `.spk`/`.orig` 는 금지(템플릿 CSS 가 숨기기도 함).

### 14.3 규칙 (프롬프트 `common.md` 2~4)
본문 한국어. 인용은 원문만, 화자 표기 없음, 페이지 어디에도 참석자 라벨("Speaker 1", "고객 측") 금지. 인용 정제는 필러·더듬는 반복 제거, 생략 `…`, 용어 교정만 — 뜻을 바꾸거나 보태지 않는다. 담당은 역할·팀만(사람 이름·화자 금지).

## 15. 페이지 기록 테이블 `transmeet-pages` (2026-10-09)

- **테이블**: PK `meetingId`, PAY_PER_REQUEST, PITR, RETAIN. 항목: `meetingId, pageId, url, title, shareSummary, generatedAt, updatedAt`. 미팅당 페이지 하나(재발행은 덮어쓰기).
- **발행(에이전트 `publish.py`)**: 페이지 테이블 GetItem 으로 이전 pageId 확인 → S3 put → `TransactWriteItems[ConditionCheck(미팅 테이블 attribute_exists(meetingId)), Put(페이지 테이블)]` → 이전 S3 객체 삭제. 미팅 항목은 **읽기만** 하므로 400 KB 항목도 통과한다. 트랜잭션 취소 사유 첫 항목이 `ConditionalCheckFailed` 면 "meeting not found", 그 외는 원래 예외(롤백은 공통).
- **백엔드 `page_agent.py`**: `load_page(ddb, pages_table, meeting_id)`; `_published_since` 와 `run_unpublish` 가 페이지 테이블을 읽고, `unpublish_page` 는 S3 삭제 뒤 페이지 테이블 DeleteItem. 미팅 항목에는 쓰지 않는다. 설정 `PAGES_TABLE`(기본 `transmeet-pages`).
- **REST Lambda `meetings`**: `GET /meetings/{id}` 가 페이지 테이블을 읽어 응답에 `page`(프런트 `MeetingPage` 모양: meetingId·updatedAt 제외)를 합친다 — 프런트는 변경 없음. `DELETE /meetings/{id}` 는 발행 HTML(S3) → 페이지 기록 → 미팅 항목 순으로 지운다(S3 삭제 실패 시 기록을 남겨 링크가 보이게 둔다). 목록 `GET /meetings` 는 전과 같이 `page` 를 내지 않는다.
- **IAM**: Runtime 역할은 페이지 테이블 GetItem·PutItem + 미팅 테이블 ConditionCheckItem(UpdateItem 제거). 태스크 역할·Lambda 역할은 페이지 테이블 RW, Lambda 역할은 pages 버킷 DeleteObject 추가.
- **마이그레이션**: `agent/scripts/migrate_pages.py --apply` 가 미팅 항목의 `page` 를 페이지 테이블로 옮기고 `REMOVE page`. 멱등. `invoke_batch.py` 의 "페이지 있음" 판정도 페이지 테이블 기준.
- **배포 순서**: 백엔드 이미지 푸시 → `./deploy.sh infra`(테이블·Lambda·Runtime 새 버전·태스크 정의 롤링) → 마이그레이션 → OSMO cadence 재생성으로 확인.
- **검증(2026-10-09)**: 배포 후 116건 마이그레이션(미팅 항목의 `page` 0건 잔존), REST `GET /meetings/{id}` 가 `page` 를 합침, WebSocket 공유 해제가 기록·S3 를 지움, REST DELETE 가 미팅·기록·HTML 을 지움(임시 미팅으로 확인). OSMO cadence(1,156 발언)는 발행 성공 — 단, 첫 재시도는 assemble 첫 토큰 대기 11분 동안 SSE 가 침묵하다 세션이 로그 없이 사라져 실패했고, **30초 하트비트**(text 없는 status, `app.py` `_with_heartbeat`)를 넣은 Runtime v7 에서 809초에 성공했다. 페이지 테이블 116건(sample-decision 은 공유 해제 테스트로 제거).
