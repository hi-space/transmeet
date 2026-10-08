from pydantic_settings import BaseSettings


class Config(BaseSettings):
    REGION: str = "us-east-1"
    MEETINGS_TABLE: str = "transmeet-meetings"
    WHISPER_ENDPOINT: str = "whisper-large"
    BEDROCK_MODEL_ID: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    QA_MODEL_ID: str = "global.anthropic.claude-sonnet-4-6"

    # ─── Meeting page agent (AgentCore Harness) ───────────────────────────────
    HARNESS_ARN: str = ""
    # 기본 모델(Opus 5.5)이 ServiceUnavailable 을 반복하면 넘어가는 대체 하네스(다른 모델). 비어 있으면 사용 안 함.
    HARNESS_FALLBACK_ARN: str = ""
    PAGES_BUCKET: str = ""
    PAGES_PUBLIC_BASE_URL: str = "https://transmeet.hi-yoo.com/p"
    PAGE_MAX_BYTES: int = 1_500_000
    # publish_page 검증 실패 시 에이전트에게 돌려주는 재시도 횟수
    PAGE_AGENT_PUBLISH_RETRIES: int = 2
    # 하네스 안에서 Bedrock 이 일시적으로 거절(ServiceUnavailable/Throttling)하면 새 세션으로 다시 시도하는 횟수
    PAGE_AGENT_INVOKE_RETRIES: int = 2

    class Config:
        env_file = ".env"


config = Config()
