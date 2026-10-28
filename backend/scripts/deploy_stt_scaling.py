#!/usr/bin/env python3
"""Create retained STT inference components with zero idle GPU capacity.

Default: validate and print the plan. --apply creates resources. --wait completes
component/autoscaling setup after endpoint provisioning. No resources are deleted.
--migrate explicitly permits updating an existing endpoint's configuration.
"""
import argparse
import json
import time

import boto3
from botocore.exceptions import ClientError
from botocore.validate import validate_parameters

from deploy_stt import MODELS


def missing(exc):
    return isinstance(exc, ClientError) and exc.response["Error"]["Code"] in (
        "ValidationException", "ResourceNotFound", "ResourceNotFoundException",
    ) and any(text in str(exc).lower() for text in ("could not find", "does not exist", "not found"))


def includes(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(includes(actual.get(k), v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            includes(a, e) for a, e in zip(actual, expected))
    return actual == expected


def wait_ready(client, method, name_key, name, status_key, seconds=2700):
    deadline = time.monotonic() + seconds
    previous = None
    while time.monotonic() < deadline:
        result = getattr(client, method)(**{name_key: name})
        status = result[status_key]
        if status != previous:
            print(f"{name}: {status}", flush=True)
            previous = status
        if status == "InService":
            return result
        if status == "Failed":
            raise RuntimeError(result.get("FailureReason", f"{name} failed"))
        time.sleep(20)
    raise TimeoutError(f"{name} is still provisioning; rerun --apply --wait to finish setup.")


def configure_autoscaling(session, component, endpoint):
    scaling = session.client("application-autoscaling")
    resource = f"inference-component/{component}"
    dimension = "sagemaker:inference-component:DesiredCopyCount"
    identity = dict(ServiceNamespace="sagemaker", ResourceId=resource, ScalableDimension=dimension)
    scaling.register_scalable_target(**identity, MinCapacity=0, MaxCapacity=1)
    scaling.put_scaling_policy(
        **identity, PolicyName=f"{component}-traffic", PolicyType="TargetTrackingScaling",
        TargetTrackingScalingPolicyConfiguration={
            "TargetValue": 1.0,
            "CustomizedMetricSpecification": {
                "Metrics": [
                    {
                        "Id": "requests",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "AWS/SageMaker", "MetricName": "Invocations",
                                "Dimensions": [
                                    {"Name": "EndpointName", "Value": endpoint},
                                    {"Name": "VariantName", "Value": "AllTraffic"},
                                ],
                            },
                            "Stat": "Sum", "Unit": "Count",
                        },
                        "ReturnData": False,
                    },
                    {
                        "Id": "active",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "TransMeet/STT", "MetricName": "ActiveUse",
                                "Dimensions": [{"Name": "InferenceComponentName", "Value": component}],
                            },
                            "Stat": "Maximum", "Unit": "Count",
                        },
                        "ReturnData": False,
                    },
                    {
                        "Id": "usage",
                        "Expression": "MAX([FILL(requests,0),FILL(active,0)])",
                        "ReturnData": True,
                    },
                ],
            },
            "ScaleInCooldown": 300, "ScaleOutCooldown": 300,
        },
    )
    policy = scaling.put_scaling_policy(
        **identity, PolicyName=f"{component}-wake", PolicyType="StepScaling",
        StepScalingPolicyConfiguration={
            "AdjustmentType": "ChangeInCapacity", "MetricAggregationType": "Maximum",
            "Cooldown": 60,
            "StepAdjustments": [{"MetricIntervalLowerBound": 0, "ScalingAdjustment": 1}],
        },
    )
    session.client("cloudwatch").put_metric_alarm(
        AlarmName=f"{component}-no-capacity",
        Namespace="AWS/SageMaker", MetricName="NoCapacityInvocationFailures",
        Dimensions=[{"Name": "InferenceComponentName", "Value": component}],
        Statistic="Sum", Period=60, EvaluationPeriods=1, DatapointsToAlarm=1,
        Threshold=0, ComparisonOperator="GreaterThanThreshold",
        TreatMissingData="notBreaching", AlarmActions=[policy["PolicyARN"]],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=MODELS)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--endpoint")
    parser.add_argument("--component")
    parser.add_argument("--model-name", help="Reuse a compatible existing SageMaker model")
    parser.add_argument("--role", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--migrate", action="store_true")
    parser.add_argument("--keep-warm", action="store_true", help="Keep one copy for immediate validation")
    parser.add_argument("--repair-component", action="store_true", help="Retry a failed component with this specification")
    args = parser.parse_args()
    spec = MODELS[args.provider]
    endpoint = args.endpoint or spec["endpoint"]
    component = args.component or f"{spec['endpoint']}-ic"
    model_name = args.model_name or f"{spec['endpoint']}-model"
    config_name = f"{endpoint}-zero-v3"
    session = boto3.Session(region_name=args.region)
    client = session.client("sagemaker")
    metadata = client.describe_hub_content(
        HubName="SageMakerPublicHub", HubContentType="Model",
        HubContentName=spec["model_id"], HubContentVersion=spec["version"],
    )
    document = json.loads(metadata["HubContentDocument"])
    instances = (["ml.g6.xlarge", "ml.g6.2xlarge"]
                 if args.provider == "qwen3-asr" else ["ml.g5.2xlarge"])
    if any(instance not in document["SupportedInferenceInstanceTypes"] for instance in instances):
        parser.error("An instance is no longer supported by the pinned model in this region.")
    variant = {
        "VariantName": "AllTraffic", "InitialInstanceCount": 1,
        "ManagedInstanceScaling": {
            "Status": "ENABLED", "MinInstanceCount": 0, "MaxInstanceCount": 1,
            "ScaleInPolicy": {"Strategy": "IDLE_RELEASE"},
        },
    }
    if len(instances) > 1:
        variant["InstancePools"] = [
            {"InstanceType": instance, "Priority": i + 1} for i, instance in enumerate(instances)
        ]
        variant["VariantInstanceProvisionTimeoutInSeconds"] = 600
    else:
        variant["InstanceType"] = instances[0]
    endpoint_config = {
        "EndpointConfigName": config_name, "ExecutionRoleArn": args.role,
        "EnableNetworkIsolation": document.get("InferenceEnableNetworkIsolation", True),
        "ProductionVariants": [variant],
    }
    specifications = []
    for instance in instances:
        resources = document.get("HostingInstanceTypeVariants", {}).get(
            "Variants", {}).get(instance, {}).get("Properties", {}).get(
                "ResourceRequirements", document.get("HostingResourceRequirements", {}))
        specifications.append({
            "InstanceType": instance, "ModelName": model_name,
            "ComputeResourceRequirements": {
                "NumberOfCpuCoresRequired": 2,
                "NumberOfAcceleratorDevicesRequired": resources.get("NumAccelerators", 1),
                "MinMemoryRequiredInMb": resources.get("MinMemoryMb", 16384),
            },
            "StartupParameters": {
                "ModelDataDownloadTimeoutInSeconds": 2400,
                "ContainerStartupHealthCheckTimeoutInSeconds": 1800,
            },
        })
    inference_component = {
        "InferenceComponentName": component, "EndpointName": endpoint,
        # The live service requires a positive count for initial creation even
        # though the SDK shape accepts zero. Scale down after it is InService.
        "VariantName": "AllTraffic", "RuntimeConfig": {"CopyCount": 1},
    }
    if len(instances) > 1:
        inference_component["Specifications"] = specifications
    else:
        inference_component["Specification"] = {
            k: v for k, v in specifications[0].items() if k != "InstanceType"
        }
    validate_parameters(endpoint_config, client.meta.service_model.operation_model("CreateEndpointConfig").input_shape)
    validate_parameters(inference_component, client.meta.service_model.operation_model("CreateInferenceComponent").input_shape)
    print(json.dumps({
        "mode": "apply" if args.apply else "plan",
        "endpoint_config": endpoint_config, "inference_component": inference_component,
        "environment": {
            spec["setting"]: endpoint,
            spec["setting"].replace("_ENDPOINT", "_INFERENCE_COMPONENT"): component,
        },
    }, indent=2), flush=True)
    if not args.apply:
        return

    try:
        model = client.describe_model(ModelName=model_name)
        source = json.dumps(model.get("PrimaryContainer", {}))
        if spec["model_id"] not in source:
            parser.error("Existing model does not reference the requested JumpStart artifact.")
    except ClientError as exc:
        if not missing(exc):
            raise
        if args.model_name:
            raise
        from sagemaker.jumpstart.model import JumpStartModel
        from sagemaker.session import Session
        model = JumpStartModel(
            model_id=spec["model_id"], model_version=spec["version"],
            name=model_name, instance_type=instances[0], role=args.role,
            sagemaker_session=Session(boto_session=session),
        )
        model.create(instance_type=instances[0], tags=[
            {"Key": "Application", "Value": "TransMeet"},
            {"Key": "SttProvider", "Value": args.provider},
        ])
    try:
        existing_config = client.describe_endpoint_config(EndpointConfigName=config_name)
        if not includes(existing_config, endpoint_config):
            parser.error("Existing endpoint configuration differs from this plan.")
    except ClientError as exc:
        if not missing(exc):
            raise
        client.create_endpoint_config(**endpoint_config)
    try:
        current = client.describe_endpoint(EndpointName=endpoint)
        if current["EndpointConfigName"] != config_name:
            if not args.migrate:
                parser.error("Use --migrate to explicitly update the existing endpoint.")
            if current["EndpointStatus"] != "InService":
                if not args.wait:
                    print("Wait until the existing endpoint is InService before updating it.", flush=True)
                    return
                wait_ready(client, "describe_endpoint", "EndpointName", endpoint, "EndpointStatus")
            client.update_endpoint(
                EndpointName=endpoint, EndpointConfigName=config_name,
                RetainAllVariantProperties=True,
            )
            print("Existing endpoint configuration update accepted.", flush=True)
    except ClientError as exc:
        if not missing(exc):
            raise
        client.create_endpoint(EndpointName=endpoint, EndpointConfigName=config_name, Tags=[
            {"Key": "Application", "Value": "TransMeet"},
            {"Key": "SttProvider", "Value": args.provider},
        ])
    if args.wait:
        wait_ready(client, "describe_endpoint", "EndpointName", endpoint, "EndpointStatus")
    elif client.describe_endpoint(EndpointName=endpoint)["EndpointStatus"] != "InService":
        print("Endpoint provisioning started; rerun with --apply --wait to finish.", flush=True)
        return
    try:
        existing = client.describe_inference_component(InferenceComponentName=component)
        if existing["EndpointName"] != endpoint:
            parser.error("Component name belongs to a different endpoint.")
        if existing["InferenceComponentStatus"] == "Failed":
            if not args.repair_component:
                raise RuntimeError(existing.get("FailureReason", "Inference component failed"))
            update = {k: v for k, v in inference_component.items()
                      if k in ("InferenceComponentName", "Specification", "Specifications", "RuntimeConfig")}
            client.update_inference_component(**update)
    except ClientError as exc:
        if not missing(exc):
            raise
        client.create_inference_component(**inference_component)
    if args.wait:
        wait_ready(client, "describe_inference_component", "InferenceComponentName",
                   component, "InferenceComponentStatus", seconds=1800)
    elif client.describe_inference_component(InferenceComponentName=component)["InferenceComponentStatus"] != "InService":
        print("Component provisioning started; rerun with --apply --wait to finish.", flush=True)
        return
    configure_autoscaling(session, component, endpoint)
    if not args.keep_warm:
        client.update_inference_component_runtime_config(
            InferenceComponentName=component, DesiredRuntimeConfig={"CopyCount": 0},
        )
    print("Retained component and native autoscaling configured; idle target is zero copies.", flush=True)


if __name__ == "__main__":
    main()
