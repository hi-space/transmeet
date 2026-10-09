import importlib


def test_page_agent_settings_defaults(monkeypatch):
    for k in ("PAGE_AGENT_RUNTIME_ARN", "PAGES_BUCKET", "PAGE_AGENT_MODEL_ID", "PAGE_AGENT_FALLBACK_MODEL_ID"):
        monkeypatch.delenv(k, raising=False)
    import app.config as cfg
    importlib.reload(cfg)
    assert cfg.config.PAGE_AGENT_RUNTIME_ARN == ""
    assert cfg.config.PAGES_BUCKET == ""
    assert cfg.config.PAGES_PUBLIC_BASE_URL == "https://transmeet.hi-yoo.com/p"
    assert cfg.config.PAGE_AGENT_MODEL_ID == "us.anthropic.claude-opus-5-5"
    assert cfg.config.PAGE_AGENT_FALLBACK_MODEL_ID == "us.anthropic.claude-opus-5"
    assert cfg.config.PAGE_AGENT_INVOKE_RETRIES == 2
    assert not hasattr(cfg.config, "HARNESS_ARN")


def test_page_agent_settings_from_env(monkeypatch):
    monkeypatch.setenv("PAGE_AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:1:runtime/x-abc")
    monkeypatch.setenv("PAGES_BUCKET", "bucket")
    import app.config as cfg
    importlib.reload(cfg)
    assert cfg.config.PAGE_AGENT_RUNTIME_ARN.endswith("runtime/x-abc")
    assert cfg.config.PAGES_BUCKET == "bucket"


def test_clients_exist():
    from app import aws_clients
    assert callable(aws_clients.agentcore_client)
    assert callable(aws_clients.s3_client)


def test_installed_botocore_supports_invoke_agent_runtime():
    import boto3
    ops = boto3.client("bedrock-agentcore", region_name="us-east-1").meta.service_model.operation_names
    assert "InvokeAgentRuntime" in ops


def test_agentcore_client_uses_long_read_timeout_without_retries():
    from app import aws_clients
    cfg = aws_clients.AGENTCORE_CLIENT_CONFIG
    assert cfg.read_timeout == 2700
    assert cfg.retries == {"max_attempts": 0}


def test_backend_model_ids_match_agent_allow_list():
    import sys
    from pathlib import Path
    agent = Path(__file__).resolve().parents[2] / "agent"
    sys.path.insert(0, str(agent))
    try:
        from runtime.model import ALLOWED_MODEL_IDS
    except ImportError:  # 에이전트 의존성(langchain-aws)이 없는 환경에서는 소스에서 직접 읽는다
        import re
        src = (agent / "runtime" / "model.py").read_text(encoding="utf-8")
        ALLOWED_MODEL_IDS = tuple(re.findall(r'_MODEL_ID = "([^"]+)"', src))
    from app import page_agent
    assert page_agent.PAGE_AGENT_MODEL_ID in ALLOWED_MODEL_IDS
    assert page_agent.PAGE_AGENT_FALLBACK_MODEL_ID in ALLOWED_MODEL_IDS
