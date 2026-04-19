from pydantic_settings import BaseSettings


class Config(BaseSettings):
    REGION: str = "us-east-1"
    MEETINGS_TABLE: str = "transmeet-meetings"
    WHISPER_ENDPOINT: str = "whisper-large"
    BEDROCK_MODEL_ID: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    QA_MODEL_ID: str = "global.anthropic.claude-sonnet-4-6"

    class Config:
        env_file = ".env"


config = Config()
