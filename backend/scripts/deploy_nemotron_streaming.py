#!/usr/bin/env python3
"""Plan or deploy the isolated, warm Nemotron streaming endpoint.

Default invocation only prints a plan. --apply creates resources; --update
explicitly permits changing this streaming endpoint to a new immutable image.
Existing batch models, endpoints, and inference components are never deleted.
"""
import argparse
import hashlib
import json
import re
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from botocore.validate import validate_parameters

ARTIFACT = (
    "s3://jumpstart-cache-prod-us-east-1/huggingface-asr/"
    "huggingface-asr-nvidia-nemotron-3-5-asr-streaming-0-6b/"
    "artifacts/inference-prepack/v1.0.2/"
)
INFERENCE_AMI = "al2-ami-sagemaker-inference-gpu-3-1"  # NVIDIA 550 / CUDA 12.4
MODEL_ENV = {
    "MODEL_DIR": "/opt/ml/model",
    "NVIDIA_VISIBLE_DEVICES": "all",
    "NVIDIA_DRIVER_CAPABILITIES": "compute,utility",
}


def make_plan(image, role, *, endpoint="transmeet-nemotron-streaming-v2", instance="ml.g5.xlarge"):
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", endpoint):
        raise ValueError("Invalid endpoint name")
    identity = json.dumps(
        [image, role, endpoint, instance, ARTIFACT, INFERENCE_AMI, MODEL_ENV],
        separators=(",", ":"), sort_keys=True,
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()[:12]
    name = f"{endpoint[:42]}-{digest}"
    tags = [{"Key": "Application", "Value": "TransMeet"}, {"Key": "SttProvider", "Value": "nemotron-streaming"}]
    return {
        "model": {
            "ModelName": name,
            "ExecutionRoleArn": role,
            "EnableNetworkIsolation": True,
            "PrimaryContainer": {
                "Image": image,
                "ModelDataSource": {"S3DataSource": {
                    "S3Uri": ARTIFACT, "S3DataType": "S3Prefix", "CompressionType": "None",
                }},
                "Environment": dict(MODEL_ENV),
            },
            "Tags": tags,
        },
        "endpoint_config": {
            "EndpointConfigName": f"{name}-cfg",
            "ProductionVariants": [{
                "VariantName": "AllTraffic", "ModelName": name,
                "InitialInstanceCount": 1, "InitialVariantWeight": 1.0,
                # g5 has local NVMe; SageMaker rejects an EBS VolumeSizeInGB override.
                "InstanceType": instance,
                "InferenceAmiVersion": INFERENCE_AMI,
                "ModelDataDownloadTimeoutInSeconds": 1800,
                "ContainerStartupHealthCheckTimeoutInSeconds": 1800,
            }],
            "Tags": tags,
        },
        "endpoint": {
            "EndpointName": endpoint,
            "EndpointConfigName": f"{name}-cfg",
            "Tags": tags,
        },
    }


def _existing(method, **kwargs):
    try:
        return method(**kwargs)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("ValidationException", "ResourceNotFound", "ResourceNotFoundException"):
            if any(term in str(exc).lower() for term in ("could not find", "not found", "does not exist")):
                return None
        raise


def _contains(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key == "Tags" or _contains(actual.get(key), value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _contains(a, b) for a, b in zip(actual, expected)
        )
    return actual == expected


def apply_plan(client, plan, *, update=False):
    desired = plan["endpoint"]
    current = _existing(client.describe_endpoint, EndpointName=desired["EndpointName"])
    changed = current and current["EndpointConfigName"] != desired["EndpointConfigName"]
    if changed and not update:
        raise ValueError("Existing endpoint has a different configuration; use --update to change it")
    for key, describe, create, name_key in (
        ("model", client.describe_model, client.create_model, "ModelName"),
        ("endpoint_config", client.describe_endpoint_config, client.create_endpoint_config, "EndpointConfigName"),
    ):
        spec = plan[key]
        existing = _existing(describe, **{name_key: spec[name_key]})
        if existing is None:
            create(**spec)
        elif not _contains(existing, spec):
            raise ValueError(f"Existing {key} has different settings: {spec[name_key]}")
    if current is None:
        client.create_endpoint(**desired)
    elif changed:
        client.update_endpoint(
            EndpointName=desired["EndpointName"], EndpointConfigName=desired["EndpointConfigName"],
        )
    return desired["EndpointName"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Immutable ECR image URI, preferably @sha256 digest")
    parser.add_argument("--role", required=True, help="Existing SageMaker execution role ARN")
    parser.add_argument("--endpoint", default="transmeet-nemotron-streaming-v2")
    parser.add_argument("--instance", default="ml.g5.xlarge")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--record", type=Path)
    args = parser.parse_args()
    if args.region != "us-east-1":
        parser.error("This pinned artifact is verified in us-east-1; select a verified regional artifact first")
    plan = make_plan(args.image, args.role, endpoint=args.endpoint, instance=args.instance)
    print(json.dumps(plan, indent=2), flush=True)
    if args.record:
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(json.dumps(plan, indent=2) + "\n")
    if not args.apply:
        return
    client = boto3.client("sagemaker", region_name=args.region)
    for key, operation in (("model", "CreateModel"), ("endpoint_config", "CreateEndpointConfig"), ("endpoint", "CreateEndpoint")):
        validate_parameters(plan[key], client.meta.service_model.operation_model(operation).input_shape)
    endpoint = apply_plan(client, plan, update=args.update)
    if args.wait:
        deadline = time.monotonic() + 2700
        while time.monotonic() < deadline:
            state = client.describe_endpoint(EndpointName=endpoint)
            status = state["EndpointStatus"]
            print(f"{endpoint}: {status}", flush=True)
            if status == "InService":
                return
            if status == "Failed":
                raise RuntimeError(state.get("FailureReason", status))
            time.sleep(20)
        raise TimeoutError("Streaming endpoint is still provisioning")


if __name__ == "__main__":
    main()
