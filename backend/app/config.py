from pydantic_settings import BaseSettings


class Config(BaseSettings):
    REGION: str = "us-east-1"
    MEETINGS_TABLE: str = "transmeet-meetings"
    WHISPER_ENDPOINT: str = "whisper-large"
    BEDROCK_MODEL_ID: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    QA_MODEL_ID: str = "global.anthropic.claude-sonnet-4-6"

    # ─── Meeting page agent (AgentCore Harness) ───────────────────────────────
    HARNESS_ARN: str = ""
    PAGES_BUCKET: str = ""
    PAGES_PUBLIC_BASE_URL: str = "https://transmeet.hi-yoo.com/p"
    PAGE_MAX_BYTES: int = 1_500_000
    # publish_page 검증 실패 시 에이전트에게 돌려주는 재시도 횟수
    PAGE_AGENT_PUBLISH_RETRIES: int = 2

    class Config:
        env_file = ".env"


config = Config()
