import os
from constructs import Construct
import aws_cdk as cdk
from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    CustomResource,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_autoscaling as autoscaling,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_logs as logs,
    aws_ecr as ecr,
    aws_codebuild as codebuild,
    aws_s3_assets as s3_assets,
    aws_lambda as _lambda,
    custom_resources as cr,
    aws_certificatemanager as acm,
    aws_route53 as route53,
    aws_route53_targets as targets,
)


class WhisperLiveKitStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- VPC ---
        vpc = ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
        )

        # --- ECR Repository ---
        ecr_repo = ecr.Repository(
            self,
            "Repo",
            repository_name="whisperlivekit",
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
        )

        # --- Upload source to S3 for CodeBuild ---
        src_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
        source_asset = s3_assets.Asset(
            self,
            "SourceAsset",
            path=src_dir,
            exclude=[".git", ".github", "__pycache__", "*.pyc"],
        )

        # --- CodeBuild Project ---
        build_project = codebuild.Project(
            self,
            "ImageBuild",
            source=codebuild.Source.s3(
                bucket=source_asset.bucket,
                path=source_asset.s3_object_key,
            ),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                privileged=True,
                compute_type=codebuild.ComputeType.LARGE,
            ),
            environment_variables={
                "ECR_REPO_URI": codebuild.BuildEnvironmentVariable(
                    value=ecr_repo.repository_uri
                ),
                "AWS_ACCOUNT_ID": codebuild.BuildEnvironmentVariable(
                    value=self.account
                ),
            },
            build_spec=codebuild.BuildSpec.from_object(
                {
                    "version": "0.2",
                    "phases": {
                        "pre_build": {
                            "commands": [
                                "aws ecr get-login-password --region $AWS_DEFAULT_REGION | "
                                "docker login --username AWS --password-stdin "
                                "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com",
                            ]
                        },
                        "build": {
                            "commands": [
                                "sed -i '/^ENTRYPOINT/i RUN uv pip install --python /app/.venv python-multipart' Dockerfile",
                                "docker build --build-arg EXTRAS=cu129 -t $ECR_REPO_URI:latest .",
                            ]
                        },
                        "post_build": {
                            "commands": [
                                "docker push $ECR_REPO_URI:latest",
                            ]
                        },
                    },
                }
            ),
            timeout=Duration.minutes(30),
        )
        ecr_repo.grant_push(build_project)
        source_asset.grant_read(build_project)

        # --- Custom Resource: trigger CodeBuild and wait for completion ---
        on_event_fn = _lambda.Function(
            self,
            "BuildOnEventFn",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=_lambda.Code.from_inline(
                "import boto3\n"
                "def handler(event, context):\n"
                "    if event['RequestType'] == 'Delete':\n"
                "        return {'PhysicalResourceId': event.get('PhysicalResourceId', 'none')}\n"
                "    cb = boto3.client('codebuild')\n"
                "    build = cb.start_build(projectName=event['ResourceProperties']['ProjectName'])\n"
                "    build_id = build['build']['id']\n"
                "    return {'PhysicalResourceId': build_id, 'Data': {'BuildId': build_id}}\n"
            ),
            timeout=Duration.minutes(1),
        )

        is_complete_fn = _lambda.Function(
            self,
            "BuildIsCompleteFn",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=_lambda.Code.from_inline(
                "import boto3\n"
                "def handler(event, context):\n"
                "    if event['RequestType'] == 'Delete':\n"
                "        return {'IsComplete': True}\n"
                "    build_id = event['PhysicalResourceId']\n"
                "    cb = boto3.client('codebuild')\n"
                "    resp = cb.batch_get_builds(ids=[build_id])\n"
                "    status = resp['builds'][0]['buildStatus']\n"
                "    if status == 'SUCCEEDED':\n"
                "        return {'IsComplete': True}\n"
                "    elif status == 'IN_PROGRESS':\n"
                "        return {'IsComplete': False}\n"
                "    else:\n"
                "        raise Exception(f'CodeBuild failed: {status}')\n"
            ),
            timeout=Duration.seconds(30),
        )

        on_event_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["codebuild:StartBuild"],
                resources=[build_project.project_arn],
            )
        )
        is_complete_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["codebuild:BatchGetBuilds"],
                resources=[build_project.project_arn],
            )
        )

        provider = cr.Provider(
            self,
            "BuildProvider",
            on_event_handler=on_event_fn,
            is_complete_handler=is_complete_fn,
            query_interval=Duration.seconds(30),
            total_timeout=Duration.minutes(30),
        )

        build_trigger = CustomResource(
            self,
            "BuildTrigger",
            service_token=provider.service_token,
            properties={
                "ProjectName": build_project.project_name,
                "SourceHash": source_asset.asset_hash,
            },
        )

        # --- ECS Cluster ---
        cluster = ecs.Cluster(
            self,
            "Cluster",
            vpc=vpc,
            container_insights_v2=ecs.ContainerInsights.DISABLED,
        )

        # --- EC2 Auto Scaling Group (GPU) ---
        instance_role = iam.Role(
            self,
            "InstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonEC2ContainerServiceforEC2Role"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagedInstanceCore"
                ),
            ],
        )

        asg = autoscaling.AutoScalingGroup(
            self,
            "Asg",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            instance_type=ec2.InstanceType("g4dn.xlarge"),
            machine_image=ecs.EcsOptimizedImage.amazon_linux2(
                hardware_type=ecs.AmiHardwareType.GPU,
            ),
            role=instance_role,
            desired_capacity=1,
            min_capacity=1,
            max_capacity=1,
            block_devices=[
                autoscaling.BlockDevice(
                    device_name="/dev/xvda",
                    volume=autoscaling.BlockDeviceVolume.ebs(
                        100,
                        volume_type=autoscaling.EbsDeviceVolumeType.GP3,
                    ),
                )
            ],
        )

        capacity_provider = ecs.AsgCapacityProvider(
            self,
            "AsgCapacityProvider",
            auto_scaling_group=asg,
            enable_managed_termination_protection=False,
        )
        cluster.add_asg_capacity_provider(capacity_provider)

        # --- ECS Task Definition ---
        task_def = ecs.Ec2TaskDefinition(
            self,
            "TaskDef",
            network_mode=ecs.NetworkMode.BRIDGE,
        )

        task_def.add_container(
            "whisper",
            image=ecs.ContainerImage.from_ecr_repository(ecr_repo, "latest"),
            memory_limit_mib=15000,
            gpu_count=1,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="wlk",
                log_retention=logs.RetentionDays.TWO_WEEKS,
            ),
            port_mappings=[
                ecs.PortMapping(container_port=8000, host_port=0),
            ],
            command=["--model", "large-v3", "--language", "auto"],
        )

        # ECS Service must wait for CodeBuild to push the image
        service = ecs.Ec2Service(
            self,
            "Service",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
        )
        service.node.add_dependency(build_trigger)

        # --- Application Load Balancer ---
        alb_sg = ec2.SecurityGroup(
            self,
            "AlbSg",
            vpc=vpc,
            description="ALB security group",
            allow_all_outbound=True,
        )
        alb_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(80),
            "Allow HTTP from internet",
        )
        alb_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "Allow HTTPS from internet",
        )

        alb = elbv2.ApplicationLoadBalancer(
            self,
            "Alb",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg,
        )

        asg.connections.allow_from(alb_sg, ec2.Port.tcp_range(32768, 65535))

        # HTTP → HTTPS redirect
        alb.add_listener(
            "HttpListener",
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            default_action=elbv2.ListenerAction.redirect(
                protocol="HTTPS",
                port="443",
                permanent=True,
            ),
        )

        # --- ACM Certificate & HTTPS Listener ---
        certificate = acm.Certificate.from_certificate_arn(
            self,
            "Cert",
            "arn:aws:acm:ap-northeast-2:913524902871:certificate/d15b916b-1516-474b-aa98-845bfd4a3055",
        )

        https_listener = alb.add_listener(
            "HttpsListener",
            port=443,
            protocol=elbv2.ApplicationProtocol.HTTPS,
            certificates=[certificate],
        )

        https_listener.add_targets(
            "EcsTarget",
            port=8000,
            targets=[
                service.load_balancer_target(
                    container_name="whisper",
                    container_port=8000,
                )
            ],
            health_check=elbv2.HealthCheck(
                path="/",
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                healthy_threshold_count=2,
                unhealthy_threshold_count=5,
            ),
            slow_start=Duration.seconds(180),
            deregistration_delay=Duration.seconds(30),
            stickiness_cookie_duration=Duration.hours(24),
        )

        # --- Route 53 ---
        zone = route53.HostedZone.from_hosted_zone_attributes(
            self,
            "Zone",
            hosted_zone_id="Z042232911ZVZ3YUUX7JL",
            zone_name="hi-yoo.com",
        )

        route53.ARecord(
            self,
            "AliasRecord",
            zone=zone,
            record_name="whisp",
            target=route53.RecordTarget.from_alias(
                targets.LoadBalancerTarget(alb)
            ),
        )

        # --- Outputs ---
        cdk.CfnOutput(
            self,
            "ALBDnsName",
            value=alb.load_balancer_dns_name,
            description="ALB DNS name for accessing WhisperLiveKit",
        )
        cdk.CfnOutput(
            self,
            "ServiceUrl",
            value="https://whisp.hi-yoo.com",
            description="HTTPS URL for WhisperLiveKit",
        )
