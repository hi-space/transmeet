#!/usr/bin/env python3
"""Print a pinned Whisper deployment; --apply and --update perform scoped changes."""
import argparse
import hashlib
import json
import re
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from botocore.validate import validate_parameters

REGION = "us-east-1"
ARTIFACT = (
    "s3://jumpstart-cache-prod-us-east-1/huggingface-asr/"
    "huggingface-asr-whisper-large-v3-turbo/artifacts/inference-prepack/v1.0.0/"
)
INFERENCE_AMI = "al2023-ami-sagemaker-inference-gpu-4-1"
MODEL_ENV = {
    "MODEL_DIR": "/opt/ml/model",
    "NVIDIA_VISIBLE_DEVICES": "all",
    "NVIDIA_DRIVER_CAPABILITIES": "compute,utility",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
}


def make_plan(
    image, role, *, region=REGION, endpoint="transmeet-whisper-streaming",
    instance="ml.g5.2xlarge",
):
    if region != REGION:
        raise ValueError("Only the verified us-east-1 artifact region is supported")
    if not re.fullmatch(
        rf"\d{{12}}\.dkr\.ecr\.{re.escape(region)}\.amazonaws\.com/"
        r"[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}", image,
    ):
        raise ValueError("An immutable regional ECR @sha256 image is required")
    if endpoint != "whisper-large" and not re.fullmatch(
        r"transmeet-whisper-streaming(?:-[a-z0-9-]{1,20})?", endpoint,
    ):
        raise ValueError("Only the existing Whisper endpoint or scoped staging endpoints are allowed")
    if instance not in ("ml.g5.xlarge", "ml.g5.2xlarge"):
        raise ValueError("Only the verified G5 instance sizes are supported")
    identity = json.dumps(
        [image, role, region, endpoint, instance, ARTIFACT, INFERENCE_AMI, MODEL_ENV],
        sort_keys=True, separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()[:12]
    name = f"{endpoint[:42]}-{digest}"
    tags = [
        {"Key": "Application", "Value": "TransMeet"},
        {"Key": "SttProvider", "Value": "whisper-streaming"},
    ]
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
                "VariantName": "AllTraffic",
                "ModelName": name,
                "InitialInstanceCount": 1,
                "InitialVariantWeight": 1.0,
                "InstanceType": instance,
                "InferenceAmiVersion": INFERENCE_AMI,
                "ModelDataDownloadTimeoutInSeconds": 1800,
                "ContainerStartupHealthCheckTimeoutInSeconds": 900,
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
        if exc.response["Error"]["Code"] in (
            "ValidationException", "ResourceNotFound", "ResourceNotFoundException",
        ) and any(term in str(exc).lower() for term in ("could not find", "not found", "does not exist")):
            return None
        raise


def _contains(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key == "Tags" or _contains(actual.get(key), value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _contains(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _check_update(current, desired, update, expected_current_config):
    production = desired["EndpointName"] == "whisper-large"
    if current is None:
        if production:
            raise ValueError("Production cutover requires the existing whisper-large endpoint")
        return False
    changed = current["EndpointConfigName"] != desired["EndpointConfigName"]
    if not changed:
        return False
    if not update:
        raise ValueError("A different configuration requires an explicit --update")
    if production and not expected_current_config:
        raise ValueError("Production update requires --expected-current-config from a fresh snapshot")
    if expected_current_config and current["EndpointConfigName"] != expected_current_config:
        raise ValueError("The live endpoint configuration changed after the snapshot")
    if current.get("EndpointStatus") != "InService":
        raise ValueError("Only an InService endpoint may be updated")
    return True


def apply_plan(client, plan, *, update=False, expected_current_config=None):
    desired = plan["endpoint"]
    current = _existing(client.describe_endpoint, EndpointName=desired["EndpointName"])
    changed = _check_update(current, desired, update, expected_current_config)
    for key, describe, create, name_key in (
        ("model", client.describe_model, client.create_model, "ModelName"),
        ("endpoint_config", client.describe_endpoint_config, client.create_endpoint_config, "EndpointConfigName"),
    ):
        specification = plan[key]
        existing = _existing(describe, **{name_key: specification[name_key]})
        if existing is None:
            create(**specification)
        elif not _contains(existing, specification):
            raise ValueError(f"Existing {key} has different settings: {specification[name_key]}")
    if current is None:
        client.create_endpoint(**desired)
    elif changed:
        # Recheck immediately before cutover. The API itself has no conditional
        # update, so this guards detectable concurrent changes rather than
        # claiming an atomic compare-and-swap.
        latest = client.describe_endpoint(EndpointName=desired["EndpointName"])
        if _check_update(latest, desired, update, current["EndpointConfigName"]):
            client.update_endpoint(
                EndpointName=desired["EndpointName"],
                EndpointConfigName=desired["EndpointConfigName"],
            )
    return desired["EndpointName"]


def verify_ready(client, plan):
    desired = plan["endpoint"]
    state = client.describe_endpoint(EndpointName=desired["EndpointName"])
    if state["EndpointStatus"] != "InService":
        raise RuntimeError(state.get("FailureReason", state["EndpointStatus"]))
    if state["EndpointConfigName"] != desired["EndpointConfigName"]:
        raise RuntimeError("Endpoint reverted to a different configuration")
    actual = client.describe_endpoint_config(EndpointConfigName=state["EndpointConfigName"])
    if not _contains(actual, plan["endpoint_config"]):
        raise RuntimeError("Endpoint configuration does not match the deployment plan")
    variants = state.get("ProductionVariants", [])
    if len(variants) != 1 or variants[0].get("CurrentInstanceCount") != 1:
        raise RuntimeError("The Whisper endpoint must have exactly one warm instance")
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--region", default=REGION)
    parser.add_argument("--endpoint", default="transmeet-whisper-streaming")
    parser.add_argument("--instance", default="ml.g5.2xlarge")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--expected-current-config")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--record", type=Path)
    args = parser.parse_args()
    plan = make_plan(
        args.image, args.role, region=args.region, endpoint=args.endpoint, instance=args.instance,
    )
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
    endpoint = apply_plan(
        client, plan, update=args.update, expected_current_config=args.expected_current_config,
    )
    if args.wait:
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            state = client.describe_endpoint(EndpointName=endpoint)
            print(f"{endpoint}: {state['EndpointStatus']}", flush=True)
            if state["EndpointStatus"] == "InService":
                verify_ready(client, plan)
                return
            if state["EndpointStatus"] == "Failed":
                raise RuntimeError(state.get("FailureReason", state["EndpointStatus"]))
            time.sleep(20)
        raise TimeoutError("Whisper endpoint is still provisioning")


if __name__ == "__main__":
    main()
