#!/usr/bin/env python3
"""Describe or explicitly deploy one of TransMeet's pinned JumpStart models.

The default command is read-only. Deployment requires --deploy and --role.
Run from the repository root: python backend/scripts/deploy_stt.py --help
The deployment environment needs SageMaker Python SDK v2 (>=2.237.1).
"""
import argparse
import json

import boto3
from botocore.exceptions import ClientError


MODELS = {
    "qwen3-asr": {
        "model_id": "huggingface-asr-qwen3-asr-1-7b",
        "version": "1.0.2",
        "instance_type": "ml.g6.xlarge",
        "endpoint": "transmeet-qwen3-asr",
        "setting": "QWEN_ASR_ENDPOINT",
        "component": "transmeet-qwen3-asr-ic-v2",
        "model_name": "transmeet-qwen3-asr-model-offline",
        "model_env": {
            "STANDARD_PIP_ARGS": "--no-deps --no-index -r /opt/ml/model/requirements.txt",
        },
    },
    "nemotron": {
        "model_id": "huggingface-asr-nvidia-nemotron-3-5-asr-streaming-0-6b",
        "version": "1.0.2",
        "instance_type": "ml.g5.2xlarge",
        "endpoint": "transmeet-nemotron-asr",
        "setting": "NEMOTRON_ASR_ENDPOINT",
        "component": "transmeet-nemotron-asr-ic-v2",
    },
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=MODELS)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--endpoint", help="New endpoint name; existing endpoints are never replaced")
    parser.add_argument("--instance-type", help="Override with another instance supported by this model")
    parser.add_argument("--deploy", action="store_true", help="Create a billed SageMaker endpoint")
    parser.add_argument("--role", help="SageMaker execution role ARN; required with --deploy")
    args = parser.parse_args()
    spec = dict(MODELS[args.provider])
    if args.instance_type:
        spec["instance_type"] = args.instance_type
    endpoint = args.endpoint or spec["endpoint"]
    session = boto3.Session(region_name=args.region)
    client = session.client("sagemaker")
    metadata = client.describe_hub_content(
        HubName="SageMakerPublicHub", HubContentType="Model",
        HubContentName=spec["model_id"], HubContentVersion=spec["version"],
    )
    document = json.loads(metadata["HubContentDocument"])
    if spec["instance_type"] not in document["SupportedInferenceInstanceTypes"]:
        parser.error("The pinned instance type is no longer supported in this region.")
    print(json.dumps({
        "provider": args.provider, **spec, "endpoint": endpoint, "region": args.region,
        "instance_count": 1, "mode": "deploy" if args.deploy else "describe",
        "license": document.get("License"),
        "environment_setting": f'{spec["setting"]}={endpoint}',
        "request_examples": list(document.get("DefaultPayloads", {})),
    }, ensure_ascii=False, indent=2), flush=True)
    if not args.deploy:
        return
    if not args.role:
        parser.error("--role is required with --deploy")
    try:
        client.describe_endpoint(EndpointName=endpoint)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ValidationException":
            raise
    else:
        parser.error(f"Endpoint {endpoint!r} already exists; refusing to replace it.")

    from sagemaker.jumpstart.model import JumpStartModel
    from sagemaker.session import Session

    model = JumpStartModel(
        model_id=spec["model_id"], model_version=spec["version"],
        instance_type=spec["instance_type"], role=args.role,
        name=spec.get("model_name"), env=dict(spec.get("model_env", {})),
        sagemaker_session=Session(boto_session=session),
    )
    model.deploy(
        initial_instance_count=1, instance_type=spec["instance_type"],
        endpoint_name=endpoint, wait=False,
        tags=[{"Key": "Application", "Value": "TransMeet"},
              {"Key": "SttProvider", "Value": args.provider}],
    )
    print(f"Deployment requested. Wait for {endpoint} to become InService before using it.")


if __name__ == "__main__":
    main()
