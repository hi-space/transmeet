import copy
import importlib.util
from pathlib import Path

import pytest
from botocore.exceptions import ClientError


def module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "deploy_nemotron_streaming.py"
    spec = importlib.util.spec_from_file_location("nemotron_deploy", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class FakeSageMaker:
    def __init__(self):
        self.models = {}
        self.configs = {}
        self.endpoints = {}
        self.changes = []

    def _describe(self, values, key):
        if key not in values:
            raise ClientError({"Error": {"Code": "ValidationException",
                                         "Message": "Could not find resource"}}, "Describe")
        return values[key]

    def describe_model(self, ModelName):
        return self._describe(self.models, ModelName)

    def describe_endpoint_config(self, EndpointConfigName):
        return self._describe(self.configs, EndpointConfigName)

    def describe_endpoint(self, EndpointName):
        return self._describe(self.endpoints, EndpointName)

    def create_model(self, **value):
        self.models[value["ModelName"]] = copy.deepcopy(value)
        self.changes.append("model")

    def create_endpoint_config(self, **value):
        for variant in value["ProductionVariants"]:
            if variant["InstanceType"].startswith("ml.g5.") and "VolumeSizeInGB" in variant:
                raise ClientError({"Error": {
                    "Code": "ValidationException",
                    "Message": "VolumeSize parameter is not allowed for the selected Instance type ml.g5.2xlarge",
                }}, "CreateEndpointConfig")
        self.configs[value["EndpointConfigName"]] = copy.deepcopy(value)
        self.changes.append("config")

    def create_endpoint(self, **value):
        self.endpoints[value["EndpointName"]] = copy.deepcopy(value)
        self.changes.append("endpoint")

    def update_endpoint(self, **value):
        self.endpoints[value["EndpointName"]] = copy.deepcopy(value)
        self.changes.append("update")


def test_single_model_endpoint_references_its_pinned_artifact_and_image():
    deploy = module()
    plan = deploy.make_plan("account.dkr.ecr.us-east-1.amazonaws.com/model@sha256:abc",
                            "arn:aws:iam::123456789012:role/test")
    model = plan["model"]
    variant = plan["endpoint_config"]["ProductionVariants"][0]
    assert model["PrimaryContainer"]["Image"].endswith("@sha256:abc")
    assert model["PrimaryContainer"]["ModelDataSource"]["S3DataSource"]["S3Uri"].endswith("/v1.0.2/")
    assert model["EnableNetworkIsolation"] is True
    assert variant["ModelName"] == model["ModelName"]
    assert variant["InitialInstanceCount"] == 1
    assert "ManagedInstanceScaling" not in variant
    # The pinned Torch wheel uses CUDA 12.4; G5's default driver AMI is CUDA 11.4.
    assert variant["InferenceAmiVersion"] == "al2-ami-sagemaker-inference-gpu-3-1"
    assert model["PrimaryContainer"]["Environment"]["NVIDIA_VISIBLE_DEVICES"] == "all"


def test_deployment_is_idempotent_and_does_not_replace_existing_models():
    deploy = module()
    client = FakeSageMaker()
    plan = deploy.make_plan("registry/model@sha256:abc", "role")
    deploy.apply_plan(client, plan)
    deploy.apply_plan(client, plan)
    assert client.changes == ["model", "config", "endpoint"]
    client.models[plan["model"]["ModelName"]]["PrimaryContainer"]["Image"] = "wrong"
    with pytest.raises(ValueError, match="different"):
        deploy.apply_plan(client, plan)


def test_existing_endpoint_update_requires_explicit_flag():
    deploy = module()
    client = FakeSageMaker()
    first = deploy.make_plan("registry/model@sha256:abc", "role")
    second = deploy.make_plan("registry/model@sha256:def", "role")
    deploy.apply_plan(client, first)
    with pytest.raises(ValueError, match="update"):
        deploy.apply_plan(client, second)
    assert client.changes == ["model", "config", "endpoint"]
    deploy.apply_plan(client, second, update=True)
    assert client.changes == ["model", "config", "endpoint", "model", "config", "update"]
