"""A Whisper cutover must be scoped, reproducible and reject stale snapshots."""
import copy
import importlib.util
from pathlib import Path

import boto3
import pytest
from botocore.validate import validate_parameters

from tests.test_qwen_streaming_deploy import SageMaker

IMAGE = "913524902871.dkr.ecr.us-east-1.amazonaws.com/transmeet-whisper-streaming@sha256:" + "a" * 64
ROLE = "arn:aws:iam::913524902871:role/test"


def module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "deploy_whisper_streaming.py"
    assert path.is_file(), "Whisper streaming deployment is missing"
    spec = importlib.util.spec_from_file_location("whisper_deploy", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class Endpoints(SageMaker):
    def update_endpoint(self, **values):
        self.endpoints[values["EndpointName"]].update(copy.deepcopy(values))
        self.changes.append("update")


def test_whisper_plan_pins_existing_weights_one_gpu_and_offline_execution():
    deploy = module()
    plan = deploy.make_plan(IMAGE, ROLE)
    model = plan["model"]
    variant = plan["endpoint_config"]["ProductionVariants"][0]
    assert model["EnableNetworkIsolation"] is True
    assert model["PrimaryContainer"]["Image"] == IMAGE
    assert model["PrimaryContainer"]["ModelDataSource"]["S3DataSource"] == {
        "S3Uri": "s3://jumpstart-cache-prod-us-east-1/huggingface-asr/"
        "huggingface-asr-whisper-large-v3-turbo/artifacts/inference-prepack/v1.0.0/",
        "S3DataType": "S3Prefix",
        "CompressionType": "None",
    }
    assert variant["InitialInstanceCount"] == 1
    assert variant["InstanceType"] == "ml.g5.2xlarge"
    assert variant["ModelName"] == model["ModelName"]
    assert plan["endpoint"]["EndpointName"] == "transmeet-whisper-streaming"
    assert model["PrimaryContainer"]["Environment"]["HF_HUB_OFFLINE"] == "1"
    client = boto3.client(
        "sagemaker", region_name="us-east-1",
        aws_access_key_id="testing", aws_secret_access_key="testing",
    )
    for key, operation in (
        ("model", "CreateModel"),
        ("endpoint_config", "CreateEndpointConfig"),
        ("endpoint", "CreateEndpoint"),
    ):
        validate_parameters(plan[key], client.meta.service_model.operation_model(operation).input_shape)


def test_whisper_plan_rejects_unrelated_endpoints_mutable_images_and_regions():
    deploy = module()
    for kwargs in (
        {"endpoint": "transmeet-qwen-streaming"},
        {"endpoint": "unrelated-production"},
        {"image": IMAGE.split("@")[0] + ":latest"},
        {"region": "ap-northeast-2"},
        {"instance": "ml.p5.48xlarge"},
    ):
        with pytest.raises(ValueError):
            deploy.make_plan(**{"image": IMAGE, "role": ROLE, **kwargs})
    first = deploy.make_plan(IMAGE, ROLE)
    second = deploy.make_plan(IMAGE.replace("a" * 64, "b" * 64), ROLE)
    assert first["model"]["ModelName"] != second["model"]["ModelName"]


def test_staging_creation_is_idempotent_and_updates_are_explicit():
    deploy = module()
    client = Endpoints()
    plan = deploy.make_plan(IMAGE, ROLE, instance="ml.g5.xlarge")
    deploy.apply_plan(client, plan)
    deploy.apply_plan(client, plan)
    assert client.changes == ["model", "config", "endpoint"]
    changed = deploy.make_plan(IMAGE.replace("a" * 64, "b" * 64), ROLE)
    with pytest.raises(ValueError, match="update"):
        deploy.apply_plan(client, changed)
    assert client.changes == ["model", "config", "endpoint"]


def test_production_update_requires_matching_live_configuration_before_any_mutation():
    deploy = module()
    client = Endpoints()
    client.endpoints["whisper-large"] = {
        "EndpointName": "whisper-large",
        "EndpointConfigName": "old-whisper-config",
        "EndpointStatus": "InService",
    }
    plan = deploy.make_plan(IMAGE, ROLE, endpoint="whisper-large")
    with pytest.raises(ValueError, match="expected"):
        deploy.apply_plan(client, plan, update=True)
    with pytest.raises(ValueError, match="changed"):
        deploy.apply_plan(client, plan, update=True, expected_current_config="stale-config")
    assert client.changes == []
    deploy.apply_plan(client, plan, update=True, expected_current_config="old-whisper-config")
    assert client.changes == ["model", "config", "update"]
    assert client.endpoints["whisper-large"]["EndpointConfigName"] == plan["endpoint"]["EndpointConfigName"]
    deploy.apply_plan(client, plan, update=True, expected_current_config="old-whisper-config")
    assert client.changes == ["model", "config", "update"]


def test_production_is_not_accidentally_created_and_busy_endpoints_are_not_updated():
    deploy = module()
    client = Endpoints()
    plan = deploy.make_plan(IMAGE, ROLE, endpoint="whisper-large")
    with pytest.raises(ValueError, match="existing"):
        deploy.apply_plan(client, plan, update=True, expected_current_config="old")
    client.endpoints["whisper-large"] = {
        "EndpointName": "whisper-large", "EndpointConfigName": "old", "EndpointStatus": "Updating",
    }
    with pytest.raises(ValueError, match="InService"):
        deploy.apply_plan(client, plan, update=True, expected_current_config="old")
    assert not client.changes


def test_readiness_rejects_rolled_back_configuration_and_zero_instances():
    deploy = module()
    client = Endpoints()
    plan = deploy.make_plan(IMAGE, ROLE)
    deploy.apply_plan(client, plan)
    current = client.endpoints[plan["endpoint"]["EndpointName"]]
    current.update(EndpointStatus="InService", ProductionVariants=[{"CurrentInstanceCount": 1}])
    assert deploy.verify_ready(client, plan)["EndpointStatus"] == "InService"
    current["EndpointConfigName"] = "old-configuration"
    with pytest.raises(RuntimeError, match="configuration"):
        deploy.verify_ready(client, plan)
    current["EndpointConfigName"] = plan["endpoint"]["EndpointConfigName"]
    current["ProductionVariants"][0]["CurrentInstanceCount"] = 0
    with pytest.raises(RuntimeError, match="one"):
        deploy.verify_ready(client, plan)
