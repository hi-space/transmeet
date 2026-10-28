"""Retained SageMaker components: prepare, observe, and release idle copies."""
import asyncio
from collections import defaultdict
import logging
import time

from .aws_clients import sagemaker_control_client, stt_metrics_client
from .config import config
from .stt import (
    active_endpoint_for,
    active_inference_component_for,
    active_region_for,
    is_streaming_provider,
    validate_provider,
)

logger = logging.getLogger(__name__)
MANAGED_PROVIDERS = ("qwen3-asr", "nemotron")


class CapacityManager:
    def __init__(self, client_factory=None, clock=None, metrics_client_factory=None):
        self.client_factory = client_factory
        self.metrics_client_factory = metrics_client_factory
        self.clock = clock or time.monotonic
        self.locks = defaultdict(asyncio.Lock)
        self.sessions = defaultdict(set)
        self.last_used = {}
        self.cache = {}
        self.was_ready = {}
        self.preparing_until = {}

    def _control_client(self, provider):
        if self.client_factory is not None:
            return self.client_factory()
        return sagemaker_control_client(region_name=active_region_for(provider))

    def _metrics_client(self, region):
        if self.metrics_client_factory is not None:
            return self.metrics_client_factory()
        return stt_metrics_client(region_name=region)

    def touch(self, provider):
        self.last_used[provider] = self.clock()

    def acquire(self, provider, connection):
        self.sessions[provider].add(connection)
        self.touch(provider)

    def release(self, provider, connection):
        self.sessions[provider].discard(connection)
        self.touch(provider)

    async def start_session(self, provider, connection):
        """Readiness and the idle-release guard change under the same lock."""
        async with self.locks[provider]:
            status = await self.status(provider, refresh=True)
            if status["status"] == "ready":
                self.acquire(provider, connection)
                await self.publish_activity()
            return status

    async def status(self, provider, refresh=False):
        identity = {"provider": provider}
        if provider == "nemotron":
            identity["streaming"] = is_streaming_provider(provider)
        try:
            validate_provider(provider)
        except (TypeError, ValueError):
            return {**identity, "status": "unavailable",
                    "message": "이 음성 인식 엔진은 아직 설정되지 않았습니다."}
        if provider not in MANAGED_PROVIDERS:
            return {**identity, "status": "ready", "managed": False}
        cached = self.cache.get(provider)
        if cached and not refresh and self.clock() - cached[0] < 2:
            return dict(cached[1])
        endpoint = active_endpoint_for(provider)
        component = active_inference_component_for(provider)
        try:
            async with self._control_client(provider) as client:
                if component:
                    ep, ic = await asyncio.gather(
                        client.describe_endpoint(EndpointName=endpoint),
                        client.describe_inference_component(InferenceComponentName=component),
                    )
                else:
                    ep = await client.describe_endpoint(EndpointName=endpoint)
                    ic = None
            instances = sum(v.get("CurrentInstanceCount", 0) for v in ep.get("ProductionVariants", []))
            result = {**identity, "managed": bool(component), "instances": instances}
            if ic is None:
                result["status"] = "ready" if ep["EndpointStatus"] == "InService" and instances else (
                    "error" if ep["EndpointStatus"] == "Failed" else "starting")
            else:
                runtime = ic.get("RuntimeConfig", {})
                desired = runtime.get("DesiredCopyCount", 0)
                current = runtime.get("CurrentCopyCount", 0)
                result.update(desiredCopies=desired, currentCopies=current)
                if ep["EndpointStatus"] == "Failed" or ic["InferenceComponentStatus"] == "Failed":
                    result.update(status="error", message="엔진 기동에 실패했습니다. 잠시 후 다시 확인해 주세요.")
                elif desired == 0 and (current > 0 or instances > 0):
                    result["status"] = "cooling"
                elif (ep["EndpointStatus"] == "InService" and
                      ic["InferenceComponentStatus"] == "InService"):
                    result["status"] = "ready" if desired and current and instances else (
                        "sleeping" if not desired and not current and not instances else "starting")
                else:
                    result["status"] = "starting"
            ready = result["status"] == "ready"
            if ready and not self.was_ready.get(provider):
                # Provisioning can take longer than the idle grace period.
                self.touch(provider)
            if ready or result["status"] in ("error", "sleeping", "cooling"):
                self.preparing_until.pop(provider, None)
            elif result.get("desiredCopies", 0) > 0:
                self.preparing_until.setdefault(provider, self.clock() + 1800)
            self.was_ready[provider] = ready
            self.cache[provider] = (self.clock(), result)
            return dict(result)
        except Exception:
            logger.exception("Cannot read STT capacity for %s", provider)
            return {**identity, "status": "unknown",
                    "message": "일시적으로 엔진 상태를 확인하지 못했습니다. 다시 확인하고 있습니다."}

    async def _set_copies(self, provider, count):
        component = active_inference_component_for(provider)
        if not component:
            raise ValueError("Inference Component is not configured")
        async with self._control_client(provider) as client:
            await client.update_inference_component_runtime_config(
                InferenceComponentName=component,
                DesiredRuntimeConfig={"CopyCount": count},
            )
        self.cache.pop(provider, None)
        self.was_ready[provider] = False
        if count:
            self.preparing_until[provider] = self.clock() + 1800
        else:
            self.preparing_until.pop(provider, None)

    async def prepare(self, provider):
        if provider not in MANAGED_PROVIDERS:
            return await self.status(provider)
        async with self.locks[provider]:
            self.touch(provider)
            status = await self.status(provider, refresh=True)
            if status["status"] in ("ready", "unavailable", "error", "unknown"):
                if status["status"] == "ready":
                    await self.publish_activity()
                return status
            if not status.get("managed") or status.get("desiredCopies", 0) > 0:
                return status
            try:
                self.preparing_until[provider] = self.clock() + 1800
                await self.publish_activity()
                await self._set_copies(provider, 1)
                return {**status, "status": "starting", "desiredCopies": 1,
                        "message": "음성 인식 엔진을 준비하고 있습니다. 몇 분 걸릴 수 있습니다."}
            except Exception:
                logger.exception("Cannot prepare STT capacity for %s", provider)
                return {**status, "status": "error",
                        "message": "엔진 준비를 시작하지 못했습니다. 잠시 후 다시 시도해 주세요."}

    async def publish_activity(self):
        metrics = defaultdict(list)
        now = self.clock()
        for provider in MANAGED_PROVIDERS:
            component = active_inference_component_for(provider)
            if not component:
                continue
            active = config.STT_IDLE_SECONDS <= 0 or bool(self.sessions[provider]) or (
                now - self.last_used.get(provider, float("-inf")) < config.STT_IDLE_SECONDS
            ) or now < self.preparing_until.get(provider, 0)
            metrics[active_region_for(provider)].append({
                "MetricName": "ActiveUse",
                "Dimensions": [{"Name": "InferenceComponentName", "Value": component}],
                "Value": 1.0 if active else 0.0, "Unit": "Count",
            })
        for region, data in metrics.items():
            try:
                async with self._metrics_client(region) as client:
                    await client.put_metric_data(Namespace="TransMeet/STT", MetricData=data)
            except Exception:
                logger.exception("Cannot publish STT activity metrics in %s", region)

    async def release_idle(self):
        if config.STT_IDLE_SECONDS <= 0:
            return
        for provider in MANAGED_PROVIDERS:
            if not active_inference_component_for(provider) or self.sessions[provider]:
                continue
            async with self.locks[provider]:
                if self.sessions[provider]:
                    continue
                status = await self.status(provider, refresh=True)
                if (not self.sessions[provider] and status["status"] == "ready" and
                        self.clock() - self.last_used.get(provider, self.clock()) >= config.STT_IDLE_SECONDS):
                    try:
                        await self._set_copies(provider, 0)
                        logger.info("Released idle STT copies for %s; endpoint retained", provider)
                    except Exception:
                        logger.exception("Cannot release idle STT copies for %s", provider)

    async def run(self):
        while True:
            await asyncio.sleep(30)
            await self.release_idle()
            await self.publish_activity()


capacity = CapacityManager()
