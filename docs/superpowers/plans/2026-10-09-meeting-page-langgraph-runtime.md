# 미팅 페이지 에이전트 v2 (LangGraph + AgentCore Runtime) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AgentCore Harness 기반 미팅 페이지 생성을, 우리가 작성한 LangGraph 에이전트를 AgentCore Runtime 컨테이너로 올린 구조로 교체한다. 페이지 결과물·공유 URL·프런트는 그대로.

**Architecture:** `agent/runtime/` 패키지가 `BedrockAgentCoreApp` 으로 `/invocations` 를 받고, LangGraph `StateGraph`(prepare → outline → [research ∥ insights ∥ diagrams] → assemble → validate → publish)를 돌며 SSE 로 `status/delta/done/error` 이벤트를 흘린다. 도구는 Runtime 세션 안에서 로컬로 실행되고, 발행 노드가 `page.html` 을 읽어 S3·DynamoDB 에 직접 기록한다. 백엔드는 `InvokeAgentRuntime` 스트림을 `page_stream` 으로 중계하고 재시도·대체 모델·`page_status` 만 담당한다.

**Tech Stack:** Python 3.13, langgraph 1.2.14, langchain-core 1.6.9, langchain-aws 1.8.1 (`ChatBedrockConverse`), mcp 2.3.0, bedrock-agentcore 1.24.1, boto3; CDK 2.243 (`aws-bedrockagentcore.CfnRuntime`, `aws-ecr-assets.DockerImageAsset` ARM64); 백엔드 FastAPI + aiobotocore.

**Spec:** `docs/superpowers/specs/2026-10-09-meeting-page-langgraph-runtime-design.md`

## Global Constraints

- 패키지 버전 고정: `langgraph==1.2.14`, `langchain-core==1.6.9`, `langchain-aws==1.8.1`, `mcp==2.3.0`, `bedrock-agentcore==1.24.1`, `boto3==1.43.110`. 로컬 실행·테스트는 `agent/.venv/bin/python`.
- 모델 ID: 기본 `us.anthropic.claude-opus-5-5`, 대체 `us.anthropic.claude-opus-5`. 허용 목록은 이 둘.
- SSE 이벤트 `phase` ∈ {`status`,`delta`,`done`,`error`}. `done`/`error` 는 스트림 마지막에 정확히 한 번.
- 도구 경로는 `work_dir` 밖을 절대 허용하지 않는다(`..`, 절대 경로, 심볼릭 링크 탈출 거절).
- `web_search` 노드당 12회 상한. 전체 그래프 40분 예산.
- 검증기는 `agent/skills/meeting-minutes-page/scripts/validate_page.py` 하나가 원본. 백엔드 사본은 삭제한다.
- Runtime 이름 `transmeet_meeting_page`, PUBLIC 네트워크, HTTP 프로토콜, lifecycle idle 600 / max 3600.
- 한국어 UI 문구는 기존 문구를 유지한다("미팅 기록을 불러오는 중", "페이지가 발행되었습니다" 등).

## Review Focus

1. **빈 리서치 대상**: outline 이 `research_targets=[]` 를 내면 research 노드는 모델 호출 없이 "리서치 대상이 되는 외부 사실이 없었습니다" 조각을 써야 한다 (Task 5 테스트).
2. **검증 실패 2회 후 3회째**: assemble 로 되돌리지 않고 `error` 로 끝나야 한다 (Task 5).
3. **Bedrock 일시 오류 분류**: `ServiceUnavailableException`·`ThrottlingException`·`ReadTimeout` 은 `transient=true`, 그 외 `ValidationException` 등은 `false` (Task 4).
4. **스트림이 done/error 없이 끝남**: 백엔드는 `error` 일시 취급으로 재시도하되, 재시도 전 DynamoDB 의 `page.generatedAt` 이 시작 시각 이후면 `done` 으로 마무리해 중복 생성을 막는다 (Task 9).
5. **edit_file 다중 일치**: `old` 가 2회 이상 나오면 거절하고 횟수를 알려 준다 (Task 1).

---

### Task 1: 런타임 패키지 골격 + 로컬 파일/셸 도구

**Files:**
- Create: `agent/requirements.txt`, `agent/runtime/__init__.py`, `agent/runtime/workspace.py`, `agent/runtime/tools.py`
- Create: `agent/tests/test_runtime_tools.py`
- Modify: `agent/tests/conftest.py` (`agent/` 를 `sys.path` 에 추가, 백엔드 경로 제거)
- Modify: `agent/pytest.ini` 생성 (`asyncio_mode = auto`)

**Interfaces:**
- Produces:
  - `workspace.Workspace(root: Path, skills_dir: Path)` with `resolve(rel: str) -> Path` (경계 밖이면 `ToolPathError`), `write_json(name, obj)`, `read_text(rel)`.
  - `workspace.create_workspace(session_id: str, base: Path | None = None) -> Workspace` — `base/<session_id>/` 생성, `skills` 심볼릭 링크(`SKILLS_DIR` 환경변수, 기본 `<repo>/agent/skills`), `diagrams/` 생성.
  - `tools.make_tools(ws: Workspace, names: list[str]) -> list[BaseTool]` — `load_skill`, `read_file`, `write_file`, `edit_file`, `bash` 중 요청한 것만 LangChain `StructuredTool` 로 반환.
  - `tools.SKILL_NAMES = ("meeting-minutes-page","meeting-research","meeting-insights","meeting-diagrams")`
  - `tools.BASH_OUTPUT_LIMIT = 20_000`, `tools.BASH_DEFAULT_TIMEOUT_S = 120`

- [x] **Step 1: 실패하는 테스트**

```python
# agent/tests/test_runtime_tools.py
import os, pytest
from runtime.workspace import create_workspace, ToolPathError
from runtime import tools

@pytest.fixture
def ws(tmp_path):
    return create_workspace("s" * 33, base=tmp_path)

def names(ts): return {t.name for t in ts}

def test_make_tools_filters_by_name(ws):
    assert names(tools.make_tools(ws, ["bash", "read_file"])) == {"bash", "read_file"}

def test_write_read_edit_roundtrip(ws):
    t = {x.name: x for x in tools.make_tools(ws, ["write_file", "read_file", "edit_file"])}
    assert "wrote" in t["write_file"].invoke({"path": "a.txt", "content": "hello world"})
    assert t["read_file"].invoke({"path": "a.txt"}) == "hello world"
    assert "ok" in t["edit_file"].invoke({"path": "a.txt", "old": "world", "new": "there"})
    assert (ws.root / "a.txt").read_text() == "hello there"

def test_edit_file_requires_exactly_one_match(ws):
    t = {x.name: x for x in tools.make_tools(ws, ["write_file", "edit_file"])}
    t["write_file"].invoke({"path": "a.txt", "content": "x x"})
    out = t["edit_file"].invoke({"path": "a.txt", "old": "x", "new": "y"})
    assert "2" in out and "error" in out.lower()
    out = t["edit_file"].invoke({"path": "a.txt", "old": "zz", "new": "y"})
    assert "error" in out.lower()

@pytest.mark.parametrize("bad", ["../x", "/etc/passwd", "skills/../../x"])
def test_paths_outside_workspace_rejected(ws, bad):
    t = {x.name: x for x in tools.make_tools(ws, ["read_file"])}
    assert "error" in t["read_file"].invoke({"path": bad}).lower()

def test_bash_runs_in_workspace_and_truncates(ws):
    t = {x.name: x for x in tools.make_tools(ws, ["bash"])}
    assert t["bash"].invoke({"command": "pwd"}).strip().endswith(ws.root.name)
    out = t["bash"].invoke({"command": "python3 -c \"print('a'*50000)\""})
    assert len(out) < tools.BASH_OUTPUT_LIMIT + 200 and "truncated" in out

def test_bash_timeout(ws):
    t = {x.name: x for x in tools.make_tools(ws, ["bash"])}
    assert "timed out" in t["bash"].invoke({"command": "sleep 5", "timeout_s": 1})

def test_load_skill_lists_bundle(ws):
    t = {x.name: x for x in tools.make_tools(ws, ["load_skill"])}
    out = t["load_skill"].invoke({"name": "meeting-diagrams"})
    assert "# Meeting Diagrams" in out or "meeting-diagrams" in out
    assert "scripts/timeline_svg.py" in out
    assert "error" in t["load_skill"].invoke({"name": "nope"}).lower()

def test_workspace_has_skills_link(ws):
    assert (ws.root / "skills" / "meeting-minutes-page" / "SKILL.md").exists()
```

- [x] **Step 2: 실행해 실패 확인** — `cd agent && .venv/bin/python -m pytest tests/test_runtime_tools.py -q` → `ModuleNotFoundError: runtime`
- [x] **Step 3: 구현** — `workspace.py`: `resolve` 는 `(root / rel).resolve()` 가 `root.resolve()` 하위인지 확인(`skills` 링크는 `skills_dir.resolve()` 하위도 허용). `tools.py`: 각 도구는 예외를 잡아 `"error: ..."` 문자열로 돌려준다(모델이 고칠 수 있게). `bash` 는 `subprocess.run(["bash","-lc",command], cwd=root, capture_output=True, timeout=timeout_s, env={**os.environ, "PYTHONDONTWRITEBYTECODE":"1"})`, stdout+stderr 합쳐 `BASH_OUTPUT_LIMIT` 넘으면 앞뒤 절반씩 남기고 `[... truncated N bytes ...]`. `load_skill` 은 SKILL.md 본문 + `references/`·`scripts/`·`assets/` 상대 경로 목록(`skills/<name>/...` 형식으로, 모델이 그대로 `read_file`/`bash` 에 쓰도록).
- [x] **Step 4: 통과 확인**, 기존 `agent/tests` 도 함께 통과 (`conftest` 변경으로 `test_validate_page.py` 가 백엔드 import 를 더 못 하므로 Task 3 에서 수정; 이 단계에서는 백엔드 경로를 남겨 둔다).
- [x] **Step 5: 커밋** `feat(agent): 런타임 작업 디렉터리와 로컬 파일·셸 도구`

### Task 2: 웹 검색 도구 (Gateway MCP + SigV4)

**Files:**
- Create: `agent/runtime/web_search.py`, `agent/tests/test_web_search.py`

**Interfaces:**
- Produces:
  - `SigV4Auth(httpx.Auth)` — `__init__(service: str, region: str, session: boto3.Session | None = None)`; `requires_request_body = True`.
  - `class WebSearchClient` — `__init__(gateway_url: str, region: str, *, max_calls: int = 12)`, `async def search(query: str, max_results: int = 5, include_domains: list[str] | None = None) -> str` (결과 JSON 텍스트, 상한 넘으면 `"error: web_search limit reached (12)"`), `async def aclose()`. 연결은 첫 호출에 열고 재사용(`AsyncExitStack`).
  - `make_web_search_tool(client: WebSearchClient) -> BaseTool` — 이름 `web_search`, 인자 `query`, `max_results`(기본 5, 1~10), `include_domains`(선택).
  - `GATEWAY_TOOL_NAME = "web-search-tool___WebSearch"`

- [x] **Step 1: 테스트** — (a) `SigV4Auth` 가 `Authorization`·`X-Amz-Date` 헤더를 붙이고 `host`·`content-length` 를 서명 입력에서 제외하는지(고정 자격증명으로 서명 후 헤더 존재 확인, `botocore.credentials.Credentials("AK","SK")`), (b) `WebSearchClient` 에 가짜 `call_tool`(모킹한 `ClientSession`) 을 주입해 인자 매핑(`{"query","maxResults","filters":{"domainFilter":{"include":[...]}}}`)과 12회 상한을 확인, (c) 도구 스키마에 `query` 필수.
- [x] **Step 2: 실패 확인 → Step 3: 구현** — `/tmp/gw_probe.py` 에서 검증한 코드를 옮긴다: `httpx.AsyncClient(auth=SigV4Auth(...), timeout=60)` → `streamable_http_client(url, http_client=hc)` → `ClientSession.initialize()`. `call_tool` 결과는 `content[0].text`. 세션 열기 실패·호출 예외는 `"error: ..."` 문자열로.
- [x] **Step 4: 통과 → Step 5: 커밋** `feat(agent): Gateway Web Search 를 MCP+SigV4 로 호출하는 web_search 도구`

### Task 3: 검증기·발행 모듈 (백엔드에서 이동)

**Files:**
- Create: `agent/runtime/validator.py`, `agent/runtime/publish.py`
- Move: `backend/tests/validator_corpus.py` → `agent/tests/validator_corpus.py`; `backend/tests/test_page_agent_publish.py` → `agent/tests/test_publish.py` (동기 boto3 가짜로 변경)
- Modify: `agent/tests/conftest.py`, `agent/tests/test_validate_page.py` (코퍼스 import 경로)

**Interfaces:**
- `validator.validate_html(html: str, max_bytes: int = 1_500_000) -> list[str]` — `importlib` 로 `skills/meeting-minutes-page/scripts/validate_page.py` 를 로드해 `_validate` 를 호출. `validator.REQUIRED_SECTIONS`, `REQUIRED_META` 재노출.
- `publish.PublishError(Exception)` with `.errors: list[str]`, `.transient: bool = False`
- `publish.PublishedPage` dataclass (`page_id,url,generated_at,title,share_summary`, `to_item()`), `publish.page_key(meeting_id, page_id)`
- `publish.publish_html(meeting_id: str, html: str, title: str, share_summary: str, *, s3, ddb, bucket: str, base_url: str, table: str, max_bytes: int) -> PublishedPage` — **동기** boto3 (호출자가 `asyncio.to_thread`). 로직은 백엔드 `publish_page` 와 동일(검증 → put → UpdateItem ALL_OLD → 롤백/이전 객체 삭제).
- `publish.unpublish(meeting_id, *, s3, ddb, bucket, table, page_id)` 는 백엔드에 남기므로 여기 만들지 않음.

- [x] **Step 1: 테스트 이동·수정** — `test_publish.py` 의 `FakeS3`/`FakeDDB` 를 동기 메서드로 바꾸고 `publish_html` 호출. 케이스 유지: 성공 시 Key·ContentType·CacheControl, DDB UpdateExpression·ALL_OLD, 이전 pageId 삭제, 조건 실패 → `PublishError(["meeting not found"])` + 업로드 롤백, 검증 실패(script, 외부 src, 크기) → `PublishError`.
- [x] **Step 2: 실패 확인 → Step 3: 구현 → Step 4: 통과** (`agent/tests` 전체).
- [x] **Step 5: 커밋** `feat(agent): 검증기·발행 모듈을 런타임으로 이동`

### Task 4: 모델 래퍼·이벤트·단계 실행기

**Files:**
- Create: `agent/runtime/events.py`, `agent/runtime/model.py`, `agent/runtime/stage.py`, `agent/tests/test_stage.py`, `agent/tests/fakes.py`

**Interfaces:**
- `events.status(text) / delta(text) / done(page, usage) / error(message, *, transient=False, stage="")` → dict. `events.emit(evt)` 는 `get_stream_writer()` 가 있으면 보내고 없으면 무시.
- `model.ALLOWED_MODEL_IDS = ("us.anthropic.claude-opus-5-5", "us.anthropic.claude-opus-5")`
- `model.make_chat_model(model_id: str, *, region: str, max_tokens: int = 64000) -> BaseChatModel`
- `model.is_transient_error(exc) -> bool` — 마커: `serviceunavailable`, `throttling`, `modelnotready`, `internalserver`, `modelstreamerror`, `bedrock is unable to process`, `read timed out`, `readtimeout`, `connection reset`, `connection aborted`, `too many requests`.
- `model.TransientModelError(Exception)`, `model.ainvoke_with_retry(llm, messages, *, attempts=3, backoff=(5, 15)) -> AIMessage` — 일시 오류만 재시도, 소진 시 `TransientModelError`.
- `stage.StageResult(messages: list, iterations: int, usage: dict, stopped: Literal["final","max_iterations"])`
- `stage.run_stage(*, name: str, llm, system_prompt: str, user_prompt: str, tools: list[BaseTool], max_iterations: int, on_tool_start: Callable[[str, dict], str | None] | None = None) -> StageResult` — 루프: `llm.bind_tools(tools).ainvoke(messages)`; `tool_calls` 없으면 `final`; 있으면 각 도구 `ainvoke(args)` 결과를 `ToolMessage` 로 추가(도구 예외는 `"error: ..."` 문자열). 텍스트는 `events.delta`, 도구 시작은 `events.status(describe(name,args))` — `web_search` 는 `웹 검색: {query}`, `bash` 는 `명령 실행: {첫 60자}`, `write_file`/`edit_file` 은 `파일 작성: {path}`, `load_skill` 은 `스킬 로드: {name}`, `read_file` 은 없음. usage 는 `usage_metadata` 합산.

- [x] **Step 1: 테스트** — `fakes.FakeChatModel(responses: list[AIMessage])` (`BaseChatModel` 상속, `_generate` 가 순서대로 반환, `bind_tools` 는 self). 케이스: (a) 도구 호출 2회 후 final → `ToolMessage` 가 올바른 `tool_call_id` 로 들어가고 `iterations==3`, (b) `max_iterations=1` 에서 도구 호출만 반환 → `stopped=="max_iterations"`, (c) 알 수 없는 도구 이름 → `ToolMessage` 내용에 `error`, (d) `is_transient_error` 파라미터라이즈, (e) `ainvoke_with_retry` 가 ServiceUnavailable 2회 뒤 성공, 3회면 `TransientModelError`, `ValidationException` 은 즉시 raise(backoff=(0,0)).
- [x] **Step 2 → 4: 실패 → 구현 → 통과**. **Step 5: 커밋** `feat(agent): Bedrock 모델 래퍼와 도구 루프 단계 실행기`

### Task 5: 그래프·노드·프롬프트

**Files:**
- Create: `agent/runtime/prompts/common.md`, `outline.md`, `research.md`, `insights.md`, `diagrams.md`, `assemble.md`
- Create: `agent/runtime/graph.py`, `agent/tests/test_graph.py`

**Interfaces:**
- `graph.PageState(TypedDict, total=False)`: `meeting, messages, model_id, work_dir, outline, research_html, insights_html, diagram_manifest, page_path, validation_errors, assemble_attempts, published, usage, stage_errors`
- `graph.Outline(BaseModel)`: `topics: list[Topic(start, end, title, summary)]`, `research_targets: list[ResearchTarget(claim, said_at, query)]`, `diagram_plan: list[DiagramPlan(kind: Literal["timeline","flow","relations"], title, rationale)]`, `glossary_candidates: list[str]`
- `graph.GraphDeps(dataclass)`: `llm_factory: Callable[[str], BaseChatModel]`, `web_search: WebSearchClient | None`, `publisher: Callable[[str, str, str, str], Awaitable[PublishedPage]]` (meeting_id, html, title, share_summary), `skills_dir: Path`, `work_base: Path`, `max_iterations: dict[str,int] = {"research":20,"insights":6,"diagrams":12,"assemble":25}`, `max_assemble_attempts: int = 3`
- `graph.build_graph(deps: GraphDeps) -> CompiledStateGraph`
- `graph.initial_state(payload: dict, session_id: str, deps) -> PageState` (검증: `meeting.meetingId` 비어 있지 않음, `messages` ≥1, `modelId` ∈ `ALLOWED_MODEL_IDS`; 아니면 `ValueError`)
- 노드 함수 이름: `prepare, outline, research, insights, diagrams, assemble, validate, publish`; 라우터 `after_validate(state) -> "publish" | "assemble" | "fail"`; `fail` 노드는 `error` 이벤트 후 `stage_errors` 설정.
- 조립 산출물 계약: `page.html` + `page.meta.json` `{"title": str, "share_summary": str}` (없으면 `title=meeting.title`, `share_summary=""`).
- 트랜스크립트 전달: 각 단계 user prompt 에 `transcript.json` **내용을 인라인**(모델이 매번 read_file 하지 않도록) + 해당 단계 입력(outline JSON, 다른 단계 산출물 파일 경로).

- [x] **Step 1: 테스트** — 가짜 `llm_factory` 가 단계별 스크립트된 응답을 돌려주는 `FakeChatModel` 을 반환(구조화 출력은 `with_structured_output` 오버라이드로 `Outline` 반환). 케이스: (a) 정상 경로 — research 가 `write_file research.html`, insights 가 `write_file insights.html`, diagrams 가 `bash` 로 실제 `timeline_svg.py` 실행 후 manifest 작성, assemble 가 `write_file page.html`(코퍼스의 유효 HTML) + `page.meta.json` → `publish` 호출 인자 확인, `updates` 순서에 research/insights/diagrams 가 assemble 앞에 모두 등장. (b) research_targets 빈 outline → research 노드가 llm 을 호출하지 않고(`FakeChatModel.calls==0`) 고정 문구 파일 생성. (c) assemble 가 `<script>` 포함 HTML 을 두 번 쓰고 세 번째에 정상 → validate 가 두 번 되돌리고 publish 1회. (d) 세 번 모두 불량 → `fail`, `published is None`, 마지막 커스텀 이벤트 `phase=="error"` 에 첫 오류 포함. (e) `initial_state` 가 허용되지 않은 modelId·빈 messages 를 거절.
- [x] **Step 2: 실패 확인 → Step 3: 구현**. 프롬프트 요점: `common.md` 는 전작 system_prompt 1~7항에서 하네스·publish_page 문구 제거; `outline.md` 는 주제 분할 기준(시각 포함, 3~12개), 리서치 대상 규칙(meeting-research §1 요약, ≤10), 다이어그램 계획(timeline 필수 + flow/relations 조건); `research.md` 는 "`load_skill meeting-research` 먼저, `web_search` ≤12, 결과를 `research.html` 에 output-format 으로"; `insights.md` 유사; `diagrams.md` 는 "스크립트 실행은 `bash python3 skills/meeting-diagrams/scripts/...`, 산출 `diagrams/*.svg` + `diagrams/manifest.json [{file, caption, kind}]`"; `assemble.md` 는 "`load_skill meeting-minutes-page` → `references/section-rules.md` 읽기 → `assets/template.html` 을 `page.html` 로 복사(`bash cp`) → `edit_file` 로 자리표시자 채움 또는 `write_file` 로 전체 작성 → `research.html`·`insights.html`·`diagrams/manifest.json` 읽어 삽입 → `bash python3 skills/meeting-minutes-page/scripts/validate_page.py page.html` → `page.meta.json` 작성 → 마지막 메시지는 한 줄". `validation_errors` 가 있으면 assemble user prompt 에 "이전 시도 검증 오류: …; `page.html` 은 그대로 있으니 `edit_file` 로 고치라" 를 덧붙이고 같은 대화를 이어 가지 않는다(새 메시지 목록, 파일은 유지).
- [x] **Step 4: 통과 → Step 5: 커밋** `feat(agent): 미팅 페이지 LangGraph 그래프와 단계 프롬프트`

### Task 6: Runtime 앱, 로컬 실행 스크립트, 컨테이너

**Files:**
- Create: `agent/runtime/app.py`, `agent/runtime/settings.py`, `agent/Dockerfile`, `agent/.dockerignore`, `agent/tests/test_app.py`
- Rewrite: `agent/scripts/invoke_local.py`

**Interfaces:**
- `settings.Settings` (env): `REGION`(us-east-1), `PAGES_BUCKET`, `PAGES_PUBLIC_BASE_URL`, `MEETINGS_TABLE`, `WEB_SEARCH_GATEWAY_URL`, `PAGE_MAX_BYTES`(1_500_000), `SKILLS_DIR`(`/app/skills`), `WORK_BASE`(`/tmp/transmeet-work`), `GRAPH_BUDGET_S`(2400).
- `app.run_page_graph(payload: dict, *, session_id: str, deps: GraphDeps, budget_s: float) -> AsyncIterator[dict]` — `graph.astream(..., stream_mode=["custom","updates"])`; `custom` 이벤트는 그대로 yield, `updates` 의 `publish.published` 가 생기면 `done`. 예외는 `error(transient=is_transient_error(e))`. 예산 초과(`asyncio.timeout`) → `error("시간 초과")`. 종료 시 `done`/`error` 가 안 나갔으면 `error("에이전트가 결과 없이 끝났습니다")`.
- `app.app = BedrockAgentCoreApp()`; `@app.entrypoint async def invoke(payload, context)`; `if __name__ == "__main__": app.run()`.
- `invoke_local.py`: `--direct --sample <json>|<meetingId>` 는 `run_page_graph` 를 로컬에서 실행(설정은 `infra/outputs.json` 또는 env), `--runtime --sample ...` 은 boto3 `invoke_agent_runtime` 으로 호출해 SSE 라인을 출력. 둘 다 이벤트를 `[status]`/delta/`[done]` 형태로 출력.

- [x] **Step 1: 테스트** — `run_page_graph` 에 가짜 그래프(`astream` 을 흉내 내는 객체) 주입: (a) custom 이벤트 통과 + `published` 업데이트 → 마지막 `done`, (b) 예외 → 마지막 `error`, `transient` 분류, (c) 끝까지 결과 없음 → `error`, (d) 예산 0.01초 + 느린 그래프 → `error("시간 초과")`. `initial_state` ValueError → `error(transient=False)`.
- [x] **Step 2 → 4**. **Step 5: Dockerfile** — `FROM --platform=linux/arm64 python:3.13-slim`, `WORKDIR /app`, `COPY requirements.txt`, `pip install`, `COPY runtime/ ./runtime/`, `COPY skills/ ./skills/`, `ENV PYTHONUNBUFFERED=1 SKILLS_DIR=/app/skills`, `EXPOSE 8080`, `CMD ["python","-m","runtime.app"]`. `.dockerignore`: `.venv`, `evals`, `samples`, `tests`, `scripts`, `__pycache__`, `.pytest_cache`, `harness`.
- [x] **Step 6: 로컬 통합 1회** — `cd agent && .venv/bin/python scripts/invoke_local.py --direct --sample samples/short-1on1.json` (실제 Bedrock·Gateway·S3·DDB; `PAGES_BUCKET` 등은 outputs.json). 발행 URL 200 확인. 실패하면 프롬프트·도구를 고치고 반복 — **이 단계가 끝나기 전에 인프라로 넘어가지 않는다.**
- [x] **Step 7: 커밋** `feat(agent): AgentCore Runtime 앱·로컬 실행 스크립트·컨테이너 이미지`

### Task 7: 스킬 문구를 v2 실행 환경에 맞춤

**Files:**
- Modify: `agent/skills/meeting-minutes-page/SKILL.md`, `agent/skills/meeting-research/SKILL.md`, `agent/skills/meeting-diagrams/SKILL.md`

- [x] **Step 1**: `meeting-minutes-page` 작업 순서를 §5 스펙대로(3~5단계는 산출물 읽기, 7단계 `bash python3 skills/meeting-minutes-page/scripts/validate_page.py page.html`, 8단계 `page.html`+`page.meta.json` 저장으로 발행). "shell 이 없으면 code_interpreter" 문구 삭제. `publish_page` 언급 전부 제거(자주 하는 실수 항목 포함).
- [x] **Step 2**: `meeting-research` 도구 이름 `web_search` (`include_domains` 로 도메인 좁힘).
- [x] **Step 3**: `meeting-diagrams` 23행 code_interpreter 문구를 `bash` 실행 예시로.
- [x] **Step 4**: `agent/tests` 통과(`load_skill` 테스트 포함), `grep -rn "publish_page\|code_interpreter\|___WebSearch" agent/skills` 가 비어 있음. **커밋** `docs(skills): v2 런타임 도구 이름과 발행 방식에 맞춤`

### Task 8: 인프라 — 하네스 제거, Runtime 추가

**Files:**
- Modify: `infra/lib/transmeet-stack.ts`
- Rename+Rewrite: `infra/test/harness-resources.test.sh` → `infra/test/agent-resources.test.sh`
- Modify: `deploy.sh` (`skills` 명령 제거, `all` 에서 제외)

- [x] **Step 1: 테스트 스크립트 먼저 수정** — 검사: `AWS::BedrockAgentCore::Runtime` 이름 `transmeet_meeting_page`, `NetworkConfiguration.NetworkMode==='PUBLIC'`, `ProtocolConfiguration==='HTTP'`, `LifecycleConfiguration.IdleRuntimeSessionTimeout===600 && MaxLifetime===3600`, `EnvironmentVariables` 에 `PAGES_BUCKET`·`WEB_SEARCH_GATEWAY_URL`·`MEETINGS_TABLE`·`PAGES_PUBLIC_BASE_URL`; `AWS::BedrockAgentCore::Harness` 리소스 **0개**; 실행 역할 `BedrockModelInvocation` 모델 범위(기존 검사 유지, 역할 이름 `RuntimeExecutionRole`), `InvokeGateway`, pages 버킷 `s3:PutObject`, 테이블 `dynamodb:UpdateItem`; 태스크 역할 `bedrock-agentcore:InvokeAgentRuntime`, `InvokeHarness` **없음**; ECS env 에 `PAGE_AGENT_RUNTIME_ARN`·`PAGE_AGENT_MODEL_ID`·`PAGE_AGENT_FALLBACK_MODEL_ID`, `HARNESS_ARN` 없음; Gateway/Target/CloudFront/CSP 검사 유지; `AgentSkillsBucket` 없음.
- [x] **Step 2: 실행해 실패 확인** (`bash infra/test/agent-resources.test.sh`).
- [x] **Step 3: 스택 수정** — import `aws-ecr-assets`, `aws-bedrockagentcore`. 상수 `RUNTIME_NAME='transmeet_meeting_page'`. 제거: `HARNESS_NAME`, `FALLBACK_HARNESS_NAME`, `SKILL_NAMES`, `PUBLISH_PAGE_SCHEMA`, 스킬 버킷, `harnessRole`, `makeHarness`/두 하네스, 관련 출력. 추가: `const image = new ecr_assets.DockerImageAsset(this, 'MeetingPageAgentImage', { directory: path.join(__dirname, '../../agent'), platform: ecr_assets.Platform.LINUX_ARM64 })`; `runtimeRole`(신뢰 `bedrock-agentcore.amazonaws.com`, 문장: ECR `ecr:GetAuthorizationToken` `*` + `ecr:BatchGetImage`/`GetDownloadUrlForLayer` on `image.repository.repositoryArn`, Observability/CloudWatch 기존 문장, WorkloadIdentity on `workload-identity/${RUNTIME_NAME}-*`, BedrockModelInvocation 기존 flatMap, `InvokeGateway` on gateway ARN, `pagesBucket.grantPut/grantDelete`, `meetingsTable.grant(runtimeRole,'dynamodb:UpdateItem')`); `new bedrockagentcore.CfnRuntime(this, 'MeetingPageRuntime', {...})` with `environmentVariables` (gateway URL 은 `webSearchGateway.getAtt('GatewayUrl').toString()`); `runtime.addDependency(webSearchTarget)`; 태스크 역할 `InvokeAgentRuntime` on `[runtime.attrAgentRuntimeArn, `${...}/*`]`; ECS env 교체; 출력 `MeetingPageRuntimeArn`.
- [x] **Step 4: `npx cdk synth` + 테스트 통과**. `deploy.sh` 에서 `skills` 제거.
- [x] **Step 5: 커밋** `feat(infra): 하네스를 LangGraph 에이전트 AgentCore Runtime 으로 교체`

### Task 9: 백엔드 — InvokeAgentRuntime 중계

**Files:**
- Modify: `backend/app/page_agent.py`, `backend/app/config.py`, `backend/app/aws_clients.py`
- Modify: `backend/tests/test_page_agent_run.py`, `backend/tests/test_page_agent_stream.py`(삭제 또는 축소), `backend/tests/test_config.py`
- Delete: `backend/tests/test_page_agent_publish.py`, `backend/tests/validator_corpus.py` (Task 3 에서 이동)

**Interfaces:**
- `config`: `PAGE_AGENT_RUNTIME_ARN: str = ""`, `PAGE_AGENT_MODEL_ID: str = "us.anthropic.claude-opus-5-5"`, `PAGE_AGENT_FALLBACK_MODEL_ID: str = "us.anthropic.claude-opus-5"`, `PAGE_AGENT_INVOKE_RETRIES: int = 2`. 삭제: `HARNESS_ARN`, `HARNESS_FALLBACK_ARN`, `PAGE_AGENT_PUBLISH_RETRIES`, `PAGE_MAX_BYTES`.
- `page_agent.PageAgentDeps`: `runtime_arn`, `model_id`, `fallback_model_id`, `invoke_retries`, `retry_backoff`, `agentcore_client`, `s3_client`, `ddb_client`, `bucket`, `table`.
- `page_agent.parse_sse_lines(lines: AsyncIterator[bytes]) -> AsyncIterator[dict]` — `data: ` 접두 JSON 만, 빈 줄·주석 무시, 잘못된 JSON 은 로그 후 스킵.
- `page_agent.new_session_id() -> str` (≥33자: `uuid4().hex + uuid4().hex[:8]`).
- `page_agent.is_transient_agent_error(exc_or_event) -> bool` (문자열 마커 동일 + `event.get("transient") is True`).
- `run_page_generation` 흐름은 스펙 §7. `AgentStreamEnded(Exception)` 는 done/error 없이 끝났을 때.

- [x] **Step 1: 테스트 재작성** — `FakeAgentCore.invoke_agent_runtime(**kw)` 가 `{"response": FakeBody(lines)}` 반환(`FakeBody.iter_lines()` 비동기). 케이스: 정상(status/delta/done 중계, `done.page` 가 `_remember_result` 됨, `contentType`/`accept`/`runtimeSessionId` 길이 ≥33 확인), `error transient` → 새 세션 재시도 2회 후 대체 모델 ID 로 payload 교체(`payload["meeting"]["modelId"]`), 비일시 `error` 즉시 전달, 스트림 끊김(`done` 없음) → 재시도 전 `_load_meeting` 에서 새 page 발견 시 `done`, 발견 못 하면 재시도, 예외 `ServiceUnavailableException` → 일시, `page_status` 테스트 유지, `run_unpublish` 유지. `test_page_agent_stream.py` 는 `parse_sse_lines` 테스트로 교체.
- [x] **Step 2: 실패 확인 → Step 3: 구현 → Step 4: `cd backend && python3 -m pytest -q` 통과**.
- [x] **Step 5: `aws_clients.AGENTCORE_CLIENT_CONFIG` read_timeout 2700**. **커밋** `feat(backend): 하네스 대신 AgentCore Runtime 을 호출해 SSE 이벤트를 중계`

### Task 10: 배포·검증·정리

- [x] **Step 1: 인프라 배포** — `./deploy.sh infra` (이미지 빌드 ARM64 → 푸시 → Runtime 생성, 하네스 삭제). 실패 시 CFN 이벤트로 원인 확인.
- [x] **Step 2: Runtime 단독 검증** — `cd agent && .venv/bin/python scripts/invoke_local.py --runtime --sample samples/tech-review.json`: `done` 수신, URL 200, `docs.aws.amazon.com` 링크 ≥1, SVG ≥1. Runtime 로그 그룹 `/aws/bedrock-agentcore/runtimes/<id>-DEFAULT` 에서 오류 확인.
- [x] **Step 3: 백엔드 배포** — ECR 빌드(`--platform linux/amd64`)·푸시 → `aws ecs update-service --force-new-deployment` → 실행 중 태스크 digest 확인.
- [x] **Step 4: 앱 경로 검증** — `/tmp/page_probe.py` 류 WebSocket 프로브로 [SKI] 미팅 `generate_page` → `done` 까지(소요 시간·usage 기록). 페이지 URL 200, 리서치 섹션 링크 존재.
- [x] **Step 5: 정리** — 스킬 버킷 비우기·삭제(`aws s3 rb --force`), `agent/harness/` 삭제, 스펙 §12 실측값 갱신, 메모리 파일(`harness-*` 두 개를 v2 내용으로 교체), `MEMORY.md` 갱신. **커밋** `chore(page): v2 배포 후 정리와 실측 기록`
