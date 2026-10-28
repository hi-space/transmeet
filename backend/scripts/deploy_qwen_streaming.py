#!/usr/bin/env python3
"""Print a pinned Qwen plan; --apply creates, and --update permits a scoped update."""
import argparse
import hashlib
import json
import re
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from botocore.validate import validate_parameters

REGION = "ap-northeast-2"
ARTIFACT = (
    "s3://jumpstart-cache-prod-ap-northeast-2/huggingface-asr/"
    "huggingface-asr-qwen3-asr-1-7b/artifacts/inference-prepack/v1.0.0/"
)
INFERENCE_AMI = "al2023-ami-sagemaker-inference-gpu-4-1"
MODEL_ENV = {
    "MODEL_DIR": "/opt/ml/model",
    "NVIDIA_VISIBLE_DEVICES": "all",
    "NVIDIA_DRIVER_CAPABILITIES": "compute,utility",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "VLLM_WORKER_MULTIPROC_METHOD": "spawn",
}


def make_plan(image, role, *, region=REGION, endpoint="transmeet-qwen-streaming", instance="ml.g6.2xlarge"):
    if region != REGION:
        raise ValueError("Only the verified ap-northeast-2 artifact region is supported")
    if not re.fullmatch(
        rf"\d{{12}}\.dkr\.ecr\.{re.escape(region)}\.amazonaws\.com/"
        r"[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}", image,
    ):
        raise ValueError("An immutable ECR @sha256 image in the selected region is required")
    if not re.fullmatch(r"transmeet-qwen-streaming(?:-[a-z0-9-]{1,25})?", endpoint):
        raise ValueError("Only a scoped transmeet-qwen-streaming endpoint can be created")
    if instance not in ("ml.g6.xlarge", "ml.g6.2xlarge"):
        raise ValueError("Only the verified ml.g6.xlarge and ml.g6.2xlarge instances are supported")
    identity = json.dumps(
        [image, role, region, endpoint, instance, ARTIFACT, INFERENCE_AMI, MODEL_ENV],
        sort_keys=True, separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()[:12]
    name = f"{endpoint[:42]}-{digest}"
    tags = [{"Key": "Application", "Value": "TransMeet"}, {"Key": "SttProvider", "Value": "qwen-streaming"}]
    return {
        "model": {
            "ModelName": name, "ExecutionRoleArn": role, "EnableNetworkIsolation": True,
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
                "InstanceType": instance, "InferenceAmiVersion": INFERENCE_AMI,
                "ModelDataDownloadTimeoutInSeconds": 1800,
                "ContainerStartupHealthCheckTimeoutInSeconds": 1800,
            }],
            "Tags": tags,
        },
        "endpoint": {
            "EndpointName": endpoint, "EndpointConfigName": f"{name}-cfg", "Tags": tags,
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
        raise ValueError("Existing endpoint has a different configuration; it will not be changed")
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
        # SageMaker retains the serving fleet until the replacement is healthy.
        # A different G6 size can use its separate quota during this overlap.
        client.update_endpoint(
            EndpointName=desired["EndpointName"], EndpointConfigName=desired["EndpointConfigName"],
        )
    return desired["EndpointName"]


def verify_ready(client, plan):
    desired = plan["endpoint"]
    state = client.describe_endpoint(EndpointName=desired["EndpointName"])
    if state["EndpointStatus"] != "InService":
        raise RuntimeError(state.get("FailureReason", state["EndpointStatus"]))
    if state["EndpointConfigName"] != desired["EndpointConfigName"]:
        raise RuntimeError("Endpoint reverted to a different configuration")
    config = client.describe_endpoint_config(EndpointConfigName=state["EndpointConfigName"])
    if not _contains(config, plan["endpoint_config"]):
        raise RuntimeError("Endpoint configuration does not match the deployment plan")
    variants = state.get("ProductionVariants", [])
    if len(variants) != 1 or variants[0].get("CurrentInstanceCount") != 1:
        raise RuntimeError("The streaming endpoint must have exactly one warm instance")
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Immutable regional ECR image URI including @sha256")
    parser.add_argument("--role", required=True)
    parser.add_argument("--endpoint", default="transmeet-qwen-streaming")
    parser.add_argument("--instance", default="ml.g6.2xlarge")
    parser.add_argument("--region", default=REGION)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--update", action="store_true", help="Permit updating only the scoped Qwen streaming endpoint")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--record", type=Path)
    args = parser.parse_args()
    plan = make_plan(args.image, args.role, region=args.region, endpoint=args.endpoint, instance=args.instance)
    client = boto3.client("sagemaker", region_name=args.region)
    for key, operation in (
        ("model", "CreateModel"), ("endpoint_config", "CreateEndpointConfig"), ("endpoint", "CreateEndpoint"),
    ):
        validate_parameters(plan[key], client.meta.service_model.operation_model(operation).input_shape)
    print(json.dumps(plan, indent=2), flush=True)
    if args.record:
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(json.dumps(plan, indent=2) + "\n")
    if not args.apply:
        return
    endpoint = apply_plan(client, plan, update=args.update)
    if args.wait:
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            state = client.describe_endpoint(EndpointName=endpoint)
            status = state["EndpointStatus"]
            print(f"{endpoint}: {status}", flush=True)
            if status == "InService":
                verify_ready(client, plan)
                return
            if status == "Failed":
                raise RuntimeError(state.get("FailureReason", status))
            time.sleep(20)
        raise TimeoutError("Qwen endpoint is still provisioning")


if __name__ == "__main__":
    main()
