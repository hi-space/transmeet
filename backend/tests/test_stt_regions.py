import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app import aws_clients, stt, stt_capacity
from app.config import Config


@pytest.fixture
def regions(monkeypatch):
    values = stt.config.model_dump()
    values.update(
        REGION="us-east-1",
        QWEN_ASR_REGION="ap-northeast-2",
        NEMOTRON_ASR_REGION="us-west-2",
        NEMOTRON_STREAMING_REGION="ap-northeast-2",
        WHISPER_ENDPOINT="whisper-east",
        QWEN_ASR_ENDPOINT="qwen-seoul",
        NEMOTRON_ASR_ENDPOINT="nemotron-oregon",
        QWEN_ASR_INFERENCE_COMPONENT="qwen-ic",
        NEMOTRON_ASR_INFERENCE_COMPONENT="nemotron-ic",
        NEMOTRON_STREAMING_ENABLED=False,
        STT_IDLE_SECONDS=0,
    )
    settings = SimpleNamespace(**values)
    for module in (stt, stt_capacity, aws_clients):
        monkeypatch.setattr(module, "config", settings)
    return settings


def test_region_settings_accept_independent_model_locations():
    settings = Config(
        _env_file=None,
        QWEN_ASR_REGION="ap-northeast-2",
        NEMOTRON_ASR_REGION="us-west-2",
        NEMOTRON_STREAMING_REGION="us-east-1",
    )
    assert settings.QWEN_ASR_REGION == "ap-northeast-2"
    assert settings.NEMOTRON_ASR_REGION == "us-west-2"
    assert settings.NEMOTRON_STREAMING_REGION == "us-east-1"


def test_streaming_and_batch_nemotron_can_use_different_regions(regions):
    assert stt.region_for("nemotron") == "us-west-2"
    assert stt.active_region_for("nemotron") == "us-west-2"
    regions.NEMOTRON_STREAMING_ENABLED = True
    assert stt.active_region_for("nemotron") == "ap-northeast-2"
    assert stt.region_for("nemotron") == "us-west-2"


def test_blank_region_overrides_fall_back_to_application_region(regions):
    regions.QWEN_ASR_REGION = " "
    regions.NEMOTRON_ASR_REGION = ""
    regions.NEMOTRON_STREAMING_REGION = ""
    regions.NEMOTRON_STREAMING_ENABLED = True
    for provider in ("whisper", "transcribe", "qwen3-asr", "nemotron"):
        assert stt.region_for(provider) == "us-east-1"
        assert stt.active_region_for(provider) == "us-east-1"


@pytest.mark.parametrize("factory,service", [
    ("sagemaker_client", "sagemaker-runtime"),
    ("sagemaker_control_client", "sagemaker"),
    ("stt_metrics_client", "cloudwatch"),
])
async def test_aws_stt_clients_honor_an_explicit_region(regions, monkeypatch, factory, service):
    calls = []

    @asynccontextmanager
    async def create_client(service_name, **kwargs):
        calls.append((service_name, kwargs["region_name"]))
        yield object()

    monkeypatch.setattr(aws_clients._session, "create_client", create_client)
    async with getattr(aws_clients, factory)(region_name="ap-northeast-2"):
        pass
    async with getattr(aws_clients, factory)():
        pass
    assert calls == [(service, "ap-northeast-2"), (service, "us-east-1")]


@pytest.mark.parametrize("provider,region,endpoint", [
    ("whisper", "us-east-1", "whisper-east"),
    ("qwen3-asr", "ap-northeast-2", "qwen-seoul"),
    ("nemotron", "us-west-2", "nemotron-oregon"),
])
async def test_batch_inference_uses_the_matching_region(regions, monkeypatch, provider, region, endpoint):
    calls = []

    class Body:
        async def read(self):
            return json.dumps({
                "text": "hello",
                "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            }).encode()

    class Runtime:
        async def invoke_endpoint(self, **kwargs):
            calls.append(kwargs["EndpointName"])
            return {"Body": Body()}

    @asynccontextmanager
    async def client(*, region_name=None):
        calls.append(region_name)
        yield Runtime()

    monkeypatch.setattr(stt, "sagemaker_client", client)
    result = await stt.transcribe_audio(provider, b"audio", "en")
    assert result.text == "hello"
    assert calls == [region, endpoint]


async def test_capacity_reads_and_updates_the_provider_region(regions, monkeypatch):
    calls = []

    class Control:
        def __init__(self, region):
            self.region = region

        async def describe_endpoint(self, **kwargs):
            calls.append(("describe", self.region, kwargs["EndpointName"]))
            return {"EndpointStatus": "InService", "ProductionVariants": [{"CurrentInstanceCount": 1}]}

        async def describe_inference_component(self, **kwargs):
            return {
                "InferenceComponentStatus": "InService",
                "RuntimeConfig": {"DesiredCopyCount": 1, "CurrentCopyCount": 1},
            }

        async def update_inference_component_runtime_config(self, **kwargs):
            calls.append(("update", self.region, kwargs["InferenceComponentName"]))

    @asynccontextmanager
    async def client(*, region_name=None):
        yield Control(region_name)

    monkeypatch.setattr(stt_capacity, "sagemaker_control_client", client)
    manager = stt_capacity.CapacityManager()
    assert (await manager.status("qwen3-asr"))["status"] == "ready"
    await manager._set_copies("nemotron", 1)
    assert calls == [
        ("describe", "ap-northeast-2", "qwen-seoul"),
        ("update", "us-west-2", "nemotron-ic"),
    ]


async def test_activity_metrics_are_grouped_by_component_region(regions, monkeypatch):
    calls = []

    @asynccontextmanager
    async def client(*, region_name=None):
        class Metrics:
            async def put_metric_data(self, **kwargs):
                calls.append((region_name, kwargs))
        yield Metrics()

    monkeypatch.setattr(stt_capacity, "stt_metrics_client", client)
    await stt_capacity.CapacityManager().publish_activity()
    assert len(calls) == 2
    assert {
        region: [m["Dimensions"][0]["Value"] for m in request["MetricData"]]
        for region, request in calls
    } == {
        "ap-northeast-2": ["qwen-ic"],
        "us-west-2": ["nemotron-ic"],
    }
    assert all(
        request["Namespace"] == "TransMeet/STT" and all(m["Value"] == 1 for m in request["MetricData"])
        for _, request in calls
    )
