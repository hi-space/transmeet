from pydantic_settings import BaseSettings


class Config(BaseSettings):
    REGION: str = "us-east-1"
    MEETINGS_TABLE: str = "transmeet-meetings"
    # 발행된 페이지 기록(pageId·url·title·shareSummary·generatedAt). 미팅 항목은 발언 전체를 인라인으로 담아
    # 400 KB 한도에 닿을 수 있으므로 페이지 기록은 별도 테이블에 둔다.
    PAGES_TABLE: str = "transmeet-pages"
    # 내 양식 사본(meetingId 키). 회의 항목은 400 KB 한도에 가까워 따로 둔다.
    SUMMARY_SNAPSHOTS_TABLE: str = "transmeet-summary-snapshots"
    WHISPER_ENDPOINT: str = "whisper-large"
    WHISPER_STREAMING_ENABLED: bool = False
    WHISPER_STREAMING_ENDPOINT: str = ""
    WHISPER_STREAMING_REGION: str = ""
    WHISPER_STREAMING_CHUNK_MS: int = 1000
    QWEN_ASR_ENDPOINT: str = ""
    NEMOTRON_ASR_ENDPOINT: str = ""
    QWEN_ASR_REGION: str = ""
    NEMOTRON_ASR_REGION: str = ""
    QWEN_STREAMING_ENABLED: bool = False
    QWEN_STREAMING_ENDPOINT: str = ""
    QWEN_STREAMING_REGION: str = ""
    QWEN_STREAMING_CHUNK_MS: int = 1000
    NEMOTRON_STREAMING_ENABLED: bool = False
    NEMOTRON_STREAMING_ENDPOINT: str = ""
    NEMOTRON_STREAMING_REGION: str = ""
    NEMOTRON_STREAMING_CHUNK_MS: int = 560
    # Separate CRT dependency from amazon-transcribe; Docker sets the venv path.
    NEMOTRON_STREAM_PYTHON: str = ""
    QWEN_ASR_INFERENCE_COMPONENT: str = ""
    NEMOTRON_ASR_INFERENCE_COMPONENT: str = ""
    # Zero disables idle shutdown so the retained model copies stay ready.
    STT_IDLE_SECONDS: int = 0
    BEDROCK_MODEL_ID: str = "global.anthropic.claude-haiku-5-5"
    QA_MODEL_ID: str = "global.anthropic.claude-sonnet-5-5"
    # 요약 패널(WebSocket summarize). 실시간 번역 모델과 분리 — 페이지 수준의 디테일을 내야 해서 큰 모델을 쓴다.
    # 페이지 에이전트와 같은 최신 모델. Opus 5.5 는 thinking 이 항상 켜져 있고 기본 effort 는 medium.
    # global. 프로파일: 2026-10-10 us. 프로파일이 503 을 반복하고 정상일 때도 한 단어에 21~52초 걸린 반면
    # global. 은 1~7초였다. 번역(BEDROCK_MODEL_ID)·Q&A(QA_MODEL_ID)도 이미 global. 을 쓴다.
    SUMMARY_MODEL_ID: str = "global.anthropic.claude-opus-5-5"
    SUMMARY_MAX_TOKENS: int = 16384
    # Opus 5.5 는 thinking 을 끌 수 없고 기본 effort 가 medium. 78줄 기준 첫 텍스트 medium 12.8초 → low 6.6초,
    # 명사형 형식 준수는 같았다(2026-10-09 측정).
    SUMMARY_EFFORT: str = "low"
    # Bedrock 이 일시적으로 거절(ServiceUnavailable/Throttling)하면 같은 모델로 다시 시도하는 횟수.
    # 2026-10-10 01:43 UTC us. Opus 5.5 프로파일이 503 을 3~4초 만에 돌려줘 요약이 바로 실패했다.
    SUMMARY_INVOKE_RETRIES: int = 1
    # 기본 모델이 재시도 후에도 거절하면 넘어가는 대체 모델. 비어 있으면 사용 안 함.
    SUMMARY_FALLBACK_MODEL_ID: str = "global.anthropic.claude-opus-5"

    # ─── Meeting page agent (LangGraph on AgentCore Runtime) ──────────────────
    PAGE_AGENT_RUNTIME_ARN: str = ""
    # 에이전트가 쓰는 모델. agent/runtime/model.py ALLOWED_MODEL_IDS 에 있는 값만 Runtime 이 받는다.
    PAGE_AGENT_MODEL_ID: str = "global.anthropic.claude-opus-5-5"
    # 기본 모델이 ServiceUnavailable 을 반복하면 넘어가는 대체 모델. 비어 있으면 사용 안 함.
    PAGE_AGENT_FALLBACK_MODEL_ID: str = "global.anthropic.claude-opus-5"
    # Bedrock 이 일시적으로 거절(ServiceUnavailable/Throttling)하거나 스트림이 끊기면 새 세션으로 다시 시도하는 횟수
    PAGE_AGENT_INVOKE_RETRIES: int = 2
    # 공유 해제(S3 객체 삭제)에 필요. 발행은 에이전트(Runtime 실행 역할)가 한다.
    PAGES_BUCKET: str = ""
    PAGES_PUBLIC_BASE_URL: str = "https://transmeet.hi-yoo.com/p"

    # 녹음 오디오(구간 PCM + 합친 MP3). 비어 있으면 오디오를 저장하지 않는다.
    AUDIO_BUCKET: str = ""
    # 미팅 종료 메일. SES 샌드박스라 수신 주소도 개별 인증돼 있어야 한다.
    MAIL_FROM: str = "TransMeet <no-reply@hi-yoo.com>"
    APP_BASE_URL: str = "https://transmeet.hi-yoo.com"

    # WebSocket 연결의 ID 토큰 검증. 비어 있으면 모든 연결을 거절한다.
    COGNITO_USER_POOL_ID: str = ""
    COGNITO_CLIENT_ID: str = ""

    class Config:
        env_file = ".env"


config = Config()
