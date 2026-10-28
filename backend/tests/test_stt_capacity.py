import asyncio
from contextlib import asynccontextmanager

import pytest

from app import stt_capacity
from app.config import config


class Control:
    def __init__(self, desired=0, current=0, instances=0):
        self.endpoint = {"EndpointStatus": "InService", "ProductionVariants": [{"CurrentInstanceCount": instances}]}
        self.component = {
            "InferenceComponentStatus": "InService",
            "RuntimeConfig": {"DesiredCopyCount": desired, "CurrentCopyCount": current},
        }
        self.updates = []

    async def describe_endpoint(self, **kwargs):
        assert kwargs["EndpointName"] == "test-qwen"
        return self.endpoint

    async def describe_inference_component(self, **kwargs):
        assert kwargs["InferenceComponentName"] == "test-qwen-ic"
        return self.component

    async def update_inference_component_runtime_config(self, **kwargs):
        assert kwargs["InferenceComponentName"] == "test-qwen-ic"
        copies = kwargs["DesiredRuntimeConfig"]["CopyCount"]
        self.updates.append(copies)
        self.component["RuntimeConfig"]["DesiredCopyCount"] = copies
        self.component["InferenceComponentStatus"] = "Updating"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(config, "QWEN_ASR_ENDPOINT", "test-qwen")
    monkeypatch.setattr(config, "QWEN_ASR_INFERENCE_COMPONENT", "test-qwen-ic")
    monkeypatch.setattr(config, "NEMOTRON_ASR_INFERENCE_COMPONENT", "")
    monkeypatch.setattr(config, "STT_IDLE_SECONDS", 300)
    now = [1000.0]
    control = Control()

    @asynccontextmanager
    async def client():
        yield control

    class Metrics:
        async def put_metric_data(self, **kwargs):
            control.metrics = kwargs

    @asynccontextmanager
    async def metric_client():
        yield Metrics()

    manager = stt_capacity.CapacityManager(
        client_factory=client, metrics_client_factory=metric_client, clock=lambda: now[0],
    )
    return manager, control, now


@pytest.mark.parametrize(
    "desired,current,instances,status,expected",
    [
        (0, 0, 0, "InService", "sleeping"),
        (0, 0, 1, "InService", "cooling"),
        (0, 1, 1, "Updating", "cooling"),
        (1, 0, 0, "Updating", "starting"),
        (1, 1, 1, "Updating", "starting"),
        (1, 1, 1, "InService", "ready"),
        (1, 0, 0, "Failed", "error"),
    ],
)
async def test_status_requires_actual_capacity(env, desired, current, instances, status, expected):
    manager, control, _ = env
    control.component["RuntimeConfig"] = {"DesiredCopyCount": desired, "CurrentCopyCount": current}
    control.component["InferenceComponentStatus"] = status
    control.endpoint["ProductionVariants"][0]["CurrentInstanceCount"] = instances
    result = await manager.status("qwen3-asr")
    assert result["status"] == expected
    assert result["instances"] == instances


async def test_concurrent_prepare_only_requests_one_copy(env):
    manager, control, _ = env
    results = await asyncio.gather(manager.prepare("qwen3-asr"), manager.prepare("qwen3-asr"))
    assert control.updates == [1]
    assert all(result["status"] == "starting" for result in results)


async def test_active_recording_is_not_released_by_idle_worker(env):
    manager, control, now = env
    control.component["RuntimeConfig"] = {"DesiredCopyCount": 1, "CurrentCopyCount": 1}
    control.endpoint["ProductionVariants"][0]["CurrentInstanceCount"] = 1
    await manager.status("qwen3-asr")
    manager.acquire("qwen3-asr", "connection")
    now[0] += 600
    await manager.release_idle()
    assert control.updates == []


async def test_idle_release_sets_zero_without_deleting_endpoint(env):
    manager, control, now = env
    control.component["RuntimeConfig"] = {"DesiredCopyCount": 1, "CurrentCopyCount": 1}
    control.endpoint["ProductionVariants"][0]["CurrentInstanceCount"] = 1
    await manager.status("qwen3-asr")
    manager.acquire("qwen3-asr", "connection")
    manager.release("qwen3-asr", "connection")
    now[0] += 301
    await manager.release_idle()
    assert control.updates == [0]


async def test_preparing_engine_gets_grace_period_after_it_becomes_ready(env):
    manager, control, now = env
    await manager.prepare("qwen3-asr")
    now[0] += 1200
    control.component["InferenceComponentStatus"] = "InService"
    control.component["RuntimeConfig"] = {"DesiredCopyCount": 1, "CurrentCopyCount": 1}
    control.endpoint["ProductionVariants"][0]["CurrentInstanceCount"] = 1
    await manager.release_idle()
    assert control.updates == [1]
    now[0] += 301
    await manager.release_idle()
    assert control.updates == [1, 0]


async def test_unconfigured_engine_does_not_wake_any_resource(env, monkeypatch):
    manager, control, _ = env
    monkeypatch.setattr(config, "QWEN_ASR_ENDPOINT", "")
    assert (await manager.prepare("qwen3-asr"))["status"] == "unavailable"
    assert control.updates == []


async def test_session_requires_ready_capacity_before_holding_idle_guard(env):
    manager, control, _ = env
    assert (await manager.start_session("qwen3-asr", "c1"))["status"] == "sleeping"
    assert not manager.sessions["qwen3-asr"]
    control.component["RuntimeConfig"] = {"DesiredCopyCount": 1, "CurrentCopyCount": 1}
    control.endpoint["ProductionVariants"][0]["CurrentInstanceCount"] = 1
    assert (await manager.start_session("qwen3-asr", "c1"))["status"] == "ready"
    assert manager.sessions["qwen3-asr"] == {"c1"}


async def test_activity_metric_protects_silent_recording_from_native_scale_in(env):
    manager, control, now = env
    manager.acquire("qwen3-asr", "recording")
    now[0] += 1200
    await manager.publish_activity()
    assert control.metrics["Namespace"] == "TransMeet/STT"
    assert control.metrics["MetricData"][0]["Value"] == 1
    manager.release("qwen3-asr", "recording")
    now[0] += 301
    await manager.publish_activity()
    assert control.metrics["MetricData"][0]["Value"] == 0


async def test_transient_describe_failure_is_unknown_not_confirmed_failure(env):
    manager, control, _ = env

    async def unavailable(**kwargs):
        raise TimeoutError("temporary control-plane timeout")

    control.describe_endpoint = unavailable
    assert (await manager.status("qwen3-asr"))["status"] == "unknown"
