import importlib


def test_page_agent_settings_defaults(monkeypatch):
    monkeypatch.delenv("HARNESS_ARN", raising=False)
    monkeypatch.delenv("PAGES_BUCKET", raising=False)
    import app.config as cfg
    importlib.reload(cfg)
    assert cfg.config.HARNESS_ARN == ""
    assert cfg.config.PAGES_BUCKET == ""
    assert cfg.config.PAGES_PUBLIC_BASE_URL == "https://transmeet.hi-yoo.com/p"
    assert cfg.config.PAGE_MAX_BYTES == 1_500_000
    assert cfg.config.PAGE_AGENT_PUBLISH_RETRIES == 2
    assert cfg.config.PAGE_AGENT_INVOKE_RETRIES == 2


def test_page_agent_settings_from_env(monkeypatch):
    monkeypatch.setenv("HARNESS_ARN", "arn:aws:bedrock-agentcore:us-east-1:1:harness/x-abc")
    monkeypatch.setenv("PAGES_BUCKET", "bucket")
    import app.config as cfg
    importlib.reload(cfg)
    assert cfg.config.HARNESS_ARN.endswith("harness/x-abc")
    assert cfg.config.PAGES_BUCKET == "bucket"


def test_clients_exist():
    from app import aws_clients
    assert callable(aws_clients.agentcore_client)
    assert callable(aws_clients.s3_client)


def test_installed_botocore_supports_invoke_harness():
    import boto3
    ops = boto3.client("bedrock-agentcore", region_name="us-east-1").meta.service_model.operation_names
    assert "InvokeHarness" in ops


def test_agentcore_client_uses_long_read_timeout_without_retries():
    from app import aws_clients
    cfg = aws_clients.AGENTCORE_CLIENT_CONFIG
    assert cfg.read_timeout == 900
    assert cfg.retries == {"max_attempts": 0}
