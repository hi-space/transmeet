"""Deployment must install Qwen's bundled wheels without reaching a package index."""
import copy
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import botocore.session
from botocore.exceptions import ClientError
import pytest


OFFLINE_PIP_ARGS = "--no-deps --no-index -r /opt/ml/model/requirements.txt"
QWEN_MODEL = "transmeet-qwen3-asr-model-offline"
QWEN_MODEL_ID = "huggingface-asr-qwen3-asr-1-7b"


def not_found(operation):
    return ClientError(
        {"Error": {"Code": "ValidationException", "Message": "Could not find resource"}},
        operation,
    )


class SageMaker:
    def __init__(self):
        self.meta = SimpleNamespace(
            service_model=botocore.session.get_session().get_service_model("sagemaker"),
        )
        self.existing_model = None
        self.read_models = []
        self.writes = []
        self.endpoint = None
        self.component = None

    def describe_hub_content(self, **kwargs):
        return {"HubContentDocument": json.dumps({
            "SupportedInferenceInstanceTypes": [
                "ml.g6.xlarge", "ml.g6.2xlarge", "ml.g5.2xlarge",
            ],
            "InferenceEnableNetworkIsolation": True,
            "HostingResourceRequirements": {"NumAccelerators": 1, "MinMemoryMb": 16384},
        })}

    def describe_model(self, **kwargs):
        self.read_models.append(kwargs["ModelName"])
        if self.existing_model is None:
            raise not_found("DescribeModel")
        return copy.deepcopy(self.existing_model)

    def describe_endpoint_config(self, **kwargs):
        raise not_found("DescribeEndpointConfig")

    def create_endpoint_config(self, **kwargs):
        self.writes.append(("CreateEndpointConfig", kwargs))

    def describe_endpoint(self, **kwargs):
        if self.endpoint is None:
            raise not_found("DescribeEndpoint")
        return self.endpoint

    def create_endpoint(self, **kwargs):
        self.writes.append(("CreateEndpoint", kwargs))
        self.endpoint = {
            "EndpointConfigName": kwargs["EndpointConfigName"],
            "EndpointStatus": "InService",
            "ProductionVariants": [{"CurrentInstanceCount": 1}],
        }

    def update_endpoint(self, **kwargs):
        self.writes.append(("UpdateEndpoint", kwargs))
        self.endpoint["EndpointConfigName"] = kwargs["EndpointConfigName"]

    def describe_inference_component(self, **kwargs):
        if self.component is None:
            raise not_found("DescribeInferenceComponent")
        return self.component

    def create_inference_component(self, **kwargs):
        self.writes.append(("CreateInferenceComponent", kwargs))
        self.component = {
            "EndpointName": kwargs["EndpointName"],
            "InferenceComponentStatus": "InService",
        }


@pytest.fixture
def deployment(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    legacy = importlib.import_module("deploy_stt")
    scaling = importlib.import_module("deploy_stt_scaling")
    models = copy.deepcopy(legacy.MODELS)
    monkeypatch.setattr(legacy, "MODELS", models)
    monkeypatch.setattr(scaling, "MODELS", models)
    client = SageMaker()
    session = SimpleNamespace(client=lambda service: client)
    monkeypatch.setattr(legacy.boto3, "Session", lambda **kwargs: session)
    monkeypatch.setattr(scaling, "configure_autoscaling", lambda *args: None)
    constructed = []
    sdk_writes = []

    class JumpStartModel:
        def __init__(self, **kwargs):
            constructed.append(copy.deepcopy(kwargs))
            # SDK 2.257.1 adds missing defaults to the caller's dict in place.
            # A shared MODELS entry must not acquire instance-specific defaults.
            if kwargs.get("env") is not None:
                kwargs["env"]["SDK_DEFAULT"] = "catalog-value"

        def create(self, **kwargs):
            sdk_writes.append(("create", kwargs))

        def deploy(self, **kwargs):
            sdk_writes.append(("deploy", kwargs))

    model_module = ModuleType("sagemaker.jumpstart.model")
    model_module.JumpStartModel = JumpStartModel
    session_module = ModuleType("sagemaker.session")
    session_module.Session = lambda **kwargs: None
    monkeypatch.setitem(sys.modules, "sagemaker.jumpstart.model", model_module)
    monkeypatch.setitem(sys.modules, "sagemaker.session", session_module)

    def run(module, provider="qwen3-asr", *flags):
        monkeypatch.setattr(sys, "argv", [
            module.__name__, provider, "--role", "arn:aws:iam::123456789012:role/Test",
            *flags,
        ])
        module.main()

    return SimpleNamespace(
        legacy=legacy, scaling=scaling, client=client, constructed=constructed,
        sdk_writes=sdk_writes, run=run,
    )


def test_scaling_plan_discloses_offline_startup_before_apply(deployment, capsys):
    deployment.run(deployment.scaling)
    plan = json.loads(capsys.readouterr().out)

    assert plan["model"] == {
        "name": QWEN_MODEL,
        "environment": {"STANDARD_PIP_ARGS": OFFLINE_PIP_ARGS},
    }
    assert plan["endpoint_config"]["EnableNetworkIsolation"] is True
    assert plan["inference_component"]["InferenceComponentName"] == "transmeet-qwen3-asr-ic-v2"
    assert all(
        spec["ModelName"] == QWEN_MODEL
        for spec in plan["inference_component"]["Specifications"]
    )
    assert deployment.client.writes == []
    assert deployment.constructed == []


@pytest.mark.parametrize("flags,timeout,minimum", [
    ((), "0", 1),
    (("--scale-to-zero",), "300", 0),
])
def test_scaling_plan_exposes_matching_backend_idle_policy(deployment, capsys, flags, timeout, minimum):
    deployment.run(deployment.scaling, "qwen3-asr", *flags)
    plan = json.loads(capsys.readouterr().out)
    assert plan["environment"]["STT_IDLE_SECONDS"] == timeout
    variant = plan["endpoint_config"]["ProductionVariants"][0]
    assert variant["ManagedInstanceScaling"]["MinInstanceCount"] == minimum
    if minimum == 1:
        assert variant["InstancePools"][0] == {"InstanceType": "ml.g6.2xlarge", "Priority": 1}
    assert deployment.client.writes == []


@pytest.mark.parametrize("provider,setting,region", [
    ("qwen3-asr", "QWEN_ASR_REGION", "ap-northeast-2"),
    ("nemotron", "NEMOTRON_ASR_REGION", "us-west-2"),
])
def test_scaling_plan_exposes_the_model_region(deployment, capsys, provider, setting, region):
    deployment.run(deployment.scaling, provider, "--region", region)
    assert json.loads(capsys.readouterr().out)["environment"][setting] == region


@pytest.mark.parametrize("kwargs,minimum", [({}, 1), ({"keep_warm": False}, 0)])
def test_native_scaling_floor_matches_retention_policy(monkeypatch, kwargs, minimum):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    scaling = importlib.import_module("deploy_stt_scaling")
    targets = []

    class Scaling:
        def register_scalable_target(self, **request):
            targets.append(request)

        def put_scaling_policy(self, **request):
            return {"PolicyARN": "arn:aws:autoscaling:us-east-1:123456789012:scalingPolicy:test"}

    class CloudWatch:
        def put_metric_alarm(self, **request):
            pass

    clients = {"application-autoscaling": Scaling(), "cloudwatch": CloudWatch()}
    scaling.configure_autoscaling(
        SimpleNamespace(client=clients.__getitem__), "test-component", "test-endpoint", **kwargs,
    )
    assert targets == [{
        "ServiceNamespace": "sagemaker",
        "ResourceId": "inference-component/test-component",
        "ScalableDimension": "sagemaker:inference-component:DesiredCopyCount",
        "MinCapacity": minimum,
        "MaxCapacity": 1,
    }]


def test_warm_migration_does_not_retain_zero_instance_count(deployment):
    deployment.client.endpoint = {
        "EndpointConfigName": "transmeet-qwen3-asr-zero-v3",
        "EndpointStatus": "InService",
        "ProductionVariants": [{"CurrentInstanceCount": 0, "DesiredInstanceCount": 0}],
    }
    deployment.client.component = {
        "EndpointName": "transmeet-qwen3-asr",
        "InferenceComponentStatus": "InService",
    }
    deployment.run(deployment.scaling, "qwen3-asr", "--apply", "--migrate")
    update = next(
        request for operation, request in deployment.client.writes if operation == "UpdateEndpoint"
    )
    assert update["RetainAllVariantProperties"] is True
    assert update["ExcludeRetainedVariantProperties"] == [{
        "VariantPropertyType": "DesiredInstanceCount",
    }]


def test_migration_reports_rollback_instead_of_success(deployment):
    deployment.client.endpoint = {
        "EndpointConfigName": "transmeet-qwen3-asr-zero-v3",
        "EndpointStatus": "InService",
        "FailureReason": "InsufficientInstanceCapacity",
        "ProductionVariants": [{"CurrentInstanceCount": 0, "DesiredInstanceCount": 0}],
    }
    deployment.client.component = {
        "EndpointName": "transmeet-qwen3-asr",
        "InferenceComponentStatus": "InService",
    }
    deployment.client.update_endpoint = lambda **request: deployment.client.writes.append(
        ("UpdateEndpoint", request)
    )
    with pytest.raises(RuntimeError, match="InsufficientInstanceCapacity"):
        deployment.run(deployment.scaling, "qwen3-asr", "--apply", "--migrate", "--wait")


def test_legacy_plan_discloses_offline_startup_before_deploy(deployment, capsys):
    deployment.run(deployment.legacy)
    plan = json.loads(capsys.readouterr().out)

    assert plan["model_name"] == QWEN_MODEL
    assert plan["model_env"] == {"STANDARD_PIP_ARGS": OFFLINE_PIP_ARGS}
    assert deployment.client.writes == []
    assert deployment.constructed == []


@pytest.mark.parametrize("helper", ["legacy", "scaling"])
@pytest.mark.parametrize("provider", ["qwen3-asr", "nemotron"])
def test_new_models_receive_copied_provider_startup_environment(deployment, helper, provider):
    module = getattr(deployment, helper)
    flags = ["--deploy"] if helper == "legacy" else ["--apply", "--keep-warm"]
    deployment.run(module, provider, *flags)

    assert len(deployment.constructed) == 1
    kwargs = deployment.constructed[0]
    expected_env = {"STANDARD_PIP_ARGS": OFFLINE_PIP_ARGS} if provider == "qwen3-asr" else {}
    assert kwargs.get("env") == expected_env
    if provider == "qwen3-asr":
        assert kwargs["name"] == QWEN_MODEL
    elif helper == "scaling":
        assert kwargs["name"] == "transmeet-nemotron-asr-model"
    else:
        assert kwargs.get("name") is None
    assert "SDK_DEFAULT" not in module.MODELS[provider].get("model_env", {})
    assert kwargs.get("enable_network_isolation") is not False
    assert deployment.sdk_writes[0][0] == ("deploy" if helper == "legacy" else "create")


@pytest.mark.parametrize("environment", [
    {},
    {"STANDARD_PIP_ARGS": "-r /opt/ml/model/requirements.txt"},
    {"STANDARD_PIP_ARGS": "install --no-deps --no-index -r /opt/ml/model/requirements.txt"},
])
def test_reused_qwen_model_must_have_offline_wheel_arguments(deployment, environment, capsys):
    deployment.client.existing_model = {
        "PrimaryContainer": {
            "ModelDataUrl": f"s3://models/{QWEN_MODEL_ID}/model.tar.gz",
            "Environment": environment,
        },
        "EnableNetworkIsolation": True,
    }

    with pytest.raises(SystemExit) as exc:
        deployment.run(deployment.scaling, "qwen3-asr", "--apply", "--keep-warm")

    assert exc.value.code == 2
    assert "STANDARD_PIP_ARGS" in capsys.readouterr().err
    assert deployment.client.writes == []
    assert deployment.constructed == []


def test_compatible_named_qwen_model_is_reused_with_catalog_defaults(deployment):
    deployment.client.existing_model = {
        "PrimaryContainer": {
            "ModelDataUrl": f"s3://models/{QWEN_MODEL_ID}/model.tar.gz",
            "Environment": {
                "STANDARD_PIP_ARGS": OFFLINE_PIP_ARGS,
                "OTHER_CATALOG_DEFAULT": "preserved",
            },
        },
        "EnableNetworkIsolation": True,
    }
    deployment.run(
        deployment.scaling, "qwen3-asr", "--apply", "--keep-warm",
        "--model-name", "custom-offline-qwen",
    )

    assert deployment.client.read_models == ["custom-offline-qwen"]
    assert deployment.constructed == []
    component = next(
        kwargs for operation, kwargs in deployment.client.writes
        if operation == "CreateInferenceComponent"
    )
    assert all(spec["ModelName"] == "custom-offline-qwen" for spec in component["Specifications"])


def test_nemotron_reuse_does_not_require_qwen_environment(deployment):
    deployment.client.existing_model = {
        "PrimaryContainer": {
            "ModelDataUrl": (
                "s3://models/huggingface-asr-nvidia-nemotron-3-5-asr-streaming-0-6b/model.tar.gz"
            ),
        },
        "EnableNetworkIsolation": True,
    }
    deployment.run(deployment.scaling, "nemotron", "--apply", "--keep-warm")

    assert deployment.client.read_models == ["transmeet-nemotron-asr-model"]
    assert deployment.constructed == []
    component = next(
        kwargs for operation, kwargs in deployment.client.writes
        if operation == "CreateInferenceComponent"
    )
    assert component["InferenceComponentName"] == "transmeet-nemotron-asr-ic-v2"
