from pydantic_settings import BaseSettings


class Config(BaseSettings):
    REGION: str = "us-east-1"
    MEETINGS_TABLE: str = "transmeet-meetings"
    WHISPER_ENDPOINT: str = "whisper-large"
    BEDROCK_MODEL_ID: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    QA_MODEL_ID: str = "global.anthropic.claude-sonnet-4-6"

    # ─── Meeting page agent (LangGraph on AgentCore Runtime) ──────────────────
    PAGE_AGENT_RUNTIME_ARN: str = ""
    # 에이전트가 쓰는 모델. agent/runtime/model.py ALLOWED_MODEL_IDS 에 있는 값만 Runtime 이 받는다.
    PAGE_AGENT_MODEL_ID: str = "us.anthropic.claude-opus-5-5"
    # 기본 모델이 ServiceUnavailable 을 반복하면 넘어가는 대체 모델. 비어 있으면 사용 안 함.
    PAGE_AGENT_FALLBACK_MODEL_ID: str = "us.anthropic.claude-opus-5"
    # Bedrock 이 일시적으로 거절(ServiceUnavailable/Throttling)하거나 스트림이 끊기면 새 세션으로 다시 시도하는 횟수
    PAGE_AGENT_INVOKE_RETRIES: int = 2
    # 공유 해제(S3 객체 삭제)에 필요. 발행은 에이전트(Runtime 실행 역할)가 한다.
    PAGES_BUCKET: str = ""
    PAGES_PUBLIC_BASE_URL: str = "https://transmeet.hi-yoo.com/p"

    class Config:
        env_file = ".env"


config = Config()
