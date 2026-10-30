import copy
import importlib.util
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.validate import validate_parameters

IMAGE = "913524902871.dkr.ecr.ap-northeast-2.amazonaws.com/transmeet-qwen-streaming@sha256:" + "a" * 64
ROLE = "arn:aws:iam::913524902871:role/test"


def module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "deploy_qwen_streaming.py"
    spec = importlib.util.spec_from_file_location("qwen_deploy", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class SageMaker:
    def __init__(self):
        self.models = {}
        self.configs = {}
        self.endpoints = {}
        self.changes = []

    def describe(self, records, key):
        if key not in records:
            raise ClientError({"Error": {
                "Code": "ValidationException", "Message": "Could not find resource",
            }}, "Describe")
        return records[key]

    def describe_model(self, ModelName):
        return self.describe(self.models, ModelName)

    def describe_endpoint_config(self, EndpointConfigName):
        return self.describe(self.configs, EndpointConfigName)

    def describe_endpoint(self, EndpointName):
        return self.describe(self.endpoints, EndpointName)

    def create_model(self, **values):
        self.models[values["ModelName"]] = copy.deepcopy(values)
        self.changes.append("model")

    def create_endpoint_config(self, **values):
        self.configs[values["EndpointConfigName"]] = copy.deepcopy(values)
        self.changes.append("config")

    def create_endpoint(self, **values):
        self.endpoints[values["EndpointName"]] = copy.deepcopy(values)
        self.changes.append("endpoint")


def test_plan_pins_seoul_artifact_gpu_ami_one_warm_host_and_network_isolation():
    deploy = module()
    plan = deploy.make_plan(IMAGE, ROLE)
    model = plan["model"]
    variant = plan["endpoint_config"]["ProductionVariants"][0]
    assert model["PrimaryContainer"]["Image"] == IMAGE
    assert model["PrimaryContainer"]["ModelDataSource"]["S3DataSource"] == {
        "S3Uri": "s3://jumpstart-cache-prod-ap-northeast-2/huggingface-asr/"
        "huggingface-asr-qwen3-asr-1-7b/artifacts/inference-prepack/v1.0.0/",
        "S3DataType": "S3Prefix", "CompressionType": "None",
    }
    assert model["EnableNetworkIsolation"] is True
    assert variant["InstanceType"] == "ml.g6e.xlarge"
    assert variant["InitialInstanceCount"] == 1
    assert variant["InferenceAmiVersion"] == "al2023-ami-sagemaker-inference-gpu-4-1"
    assert "ManagedInstanceScaling" not in variant
    assert "VolumeSizeInGB" not in variant  # G6 already includes local NVMe.
    assert variant["ModelName"] == model["ModelName"]
    assert plan["endpoint"]["EndpointName"] == "transmeet-qwen-streaming"
    client = boto3.client(
        "sagemaker", region_name="ap-northeast-2", aws_access_key_id="testing", aws_secret_access_key="testing",
    )
    for key, operation in [
        ("model", "CreateModel"), ("endpoint_config", "CreateEndpointConfig"), ("endpoint", "CreateEndpoint"),
    ]:
        validate_parameters(plan[key], client.meta.service_model.operation_model(operation).input_shape)


def test_identity_changes_with_image_and_invalid_mutable_or_regional_images_fail():
    deploy = module()
    first = deploy.make_plan(IMAGE, ROLE)
    second = deploy.make_plan(IMAGE.replace("a" * 64, "b" * 64), ROLE)
    assert first["model"]["ModelName"] != second["model"]["ModelName"]
    for image in (IMAGE.split("@")[0] + ":latest", IMAGE.replace("ap-northeast-2", "us-east-1")):
        with pytest.raises(ValueError):
            deploy.make_plan(image, ROLE)
    with pytest.raises(ValueError, match="region"):
        deploy.make_plan(IMAGE, ROLE, region="us-east-1")


def test_apply_is_idempotent_and_updates_require_an_explicit_flag():
    deploy = module()
    client = SageMaker()
    plan = deploy.make_plan(IMAGE, ROLE)
    deploy.apply_plan(client, plan)
    deploy.apply_plan(client, plan)
    assert client.changes == ["model", "config", "endpoint"]
    changed = deploy.make_plan(IMAGE.replace("a" * 64, "b" * 64), ROLE)
    with pytest.raises(ValueError, match="different configuration"):
        deploy.apply_plan(client, changed)
    assert client.changes == ["model", "config", "endpoint"]
    client.models[plan["model"]["ModelName"]]["EnableNetworkIsolation"] = False
    with pytest.raises(ValueError, match="different"):
        deploy.apply_plan(client, plan)


def test_scoped_update_uses_the_smaller_l4_host_without_changing_batch_endpoints():
    deploy = module()
    client = SageMaker()
    plan = deploy.make_plan(IMAGE, ROLE)
    deploy.apply_plan(client, plan)
    update = deploy.make_plan(IMAGE.replace("a" * 64, "b" * 64), ROLE, instance="ml.g6.xlarge")
    updates = []

    def update_endpoint(**values):
        updates.append(values)
        client.endpoints[values["EndpointName"]] = copy.deepcopy(values)

    client.update_endpoint = update_endpoint
    deploy.apply_plan(client, update, update=True)
    assert update["endpoint_config"]["ProductionVariants"][0]["InstanceType"] == "ml.g6.xlarge"
    assert updates == [{
        "EndpointName": "transmeet-qwen-streaming",
        "EndpointConfigName": update["endpoint_config"]["EndpointConfigName"],
    }]
    assert set(client.endpoints) == {"transmeet-qwen-streaming"}


def test_ready_check_rejects_rollback_and_incorrect_instance_count():
    deploy = module()
    client = SageMaker()
    plan = deploy.make_plan(IMAGE, ROLE)
    deploy.apply_plan(client, plan)
    current = client.endpoints[plan["endpoint"]["EndpointName"]]
    current["EndpointStatus"] = "InService"
    current["ProductionVariants"] = [{"VariantName": "AllTraffic", "CurrentInstanceCount": 1}]
    assert deploy.verify_ready(client, plan)["EndpointStatus"] == "InService"
    original = current["EndpointConfigName"]
    current["EndpointConfigName"] = "rolled-back-old-configuration"
    with pytest.raises(RuntimeError, match="configuration"):
        deploy.verify_ready(client, plan)
    current["EndpointConfigName"] = original
    current["ProductionVariants"][0]["CurrentInstanceCount"] = 0
    with pytest.raises(RuntimeError, match="one"):
        deploy.verify_ready(client, plan)
