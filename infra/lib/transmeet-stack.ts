import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { NodejsFunction } from 'aws-cdk-lib/aws-lambda-nodejs';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecr_assets from 'aws-cdk-lib/aws-ecr-assets';
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as targets from 'aws-cdk-lib/aws-route53-targets';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import { Construct } from 'constructs';
import * as path from 'path';

const WHISPER_ENDPOINT = 'whisper-large';
const BEDROCK_MODEL_ID = 'global.anthropic.claude-haiku-4-5-20251001-v1:0';
const REGION = 'us-east-1';
const MEETINGS_TABLE = 'transmeet-meetings';
const ROOT_DOMAIN = 'hi-yoo.com';
const APP_DOMAIN = 'transmeet.hi-yoo.com';
const PAGE_AGENT_MODEL_ID = 'us.anthropic.claude-opus-5-5';
// Opus 5.5 가 Bedrock 에서 ServiceUnavailable 을 반복하는 동안(2026-10-08 수 시간 관측) 백엔드가 넘어가는 대체 모델.
// 백엔드 config.PAGE_AGENT_FALLBACK_MODEL_ID·agent/runtime/model.py ALLOWED_MODEL_IDS 와 같은 값이어야 한다.
const PAGE_AGENT_FALLBACK_MODEL_ID = 'us.anthropic.claude-opus-5';
// AgentCore Runtime 에 올리는 LangGraph 미팅 페이지 에이전트 (agent/runtime, 이미지는 cdk deploy 가 빌드·푸시)
const RUNTIME_NAME = 'transmeet_meeting_page';

export class TransmeetStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ─── DynamoDB Tables ───────────────────────────────────────────────────────

    const meetingsTable = new dynamodb.Table(this, 'MeetingsTable', {
      tableName: MEETINGS_TABLE,
      partitionKey: { name: 'meetingId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // GSI for listing meetings by creation date
    meetingsTable.addGlobalSecondaryIndex({
      indexName: 'createdAt-index',
      partitionKey: { name: 'status', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'createdAt', type: dynamodb.AttributeType.STRING },
    });

    // ─── IAM Role (REST Lambda functions only) ──────────────────────────────────

    const lambdaRole = new iam.Role(this, 'LambdaRole', {
      roleName: 'transmeet-lambda-role',
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          'service-role/AWSLambdaBasicExecutionRole'
        ),
      ],
    });

    // Bedrock — allow both foundation models and cross-region inference profiles (global.*)
    lambdaRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
        ],
        resources: [
          `arn:aws:bedrock:*::foundation-model/*`,
          `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
        ],
      })
    );

    // Polly
    lambdaRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['polly:SynthesizeSpeech'],
        resources: ['*'],
      })
    );

    // DynamoDB
    meetingsTable.grantReadWriteData(lambdaRole);

    // ─── Shared Lambda Config ───────────────────────────────────────────────────

    const commonEnv: Record<string, string> = {
      MEETINGS_TABLE: meetingsTable.tableName,
      WHISPER_ENDPOINT,
      BEDROCK_MODEL_ID,
      REGION,
    };

    const commonNodejsFunctionProps = {
      role: lambdaRole,
      environment: commonEnv,
      runtime: lambda.Runtime.NODEJS_22_X,
      bundling: {
        minify: true,
        sourceMap: false,
        externalModules: [],
      },
    };

    // ─── Lambda Functions (REST only) ───────────────────────────────────────────

    const meetingsFn = new NodejsFunction(this, 'MeetingsFn', {
      functionName: 'transmeet-meetings',
      entry: path.join(__dirname, '../lambda/meetings/index.ts'),
      timeout: cdk.Duration.seconds(15),
      ...commonNodejsFunctionProps,
    });

    const summarizeFn = new NodejsFunction(this, 'SummarizeFn', {
      functionName: 'transmeet-summarize',
      entry: path.join(__dirname, '../lambda/summarize/index.ts'),
      timeout: cdk.Duration.minutes(5),
      ...commonNodejsFunctionProps,
      environment: {
        ...commonEnv,
        // Opus 4.7 for highest-quality meeting summaries
        SUMMARIZE_MODEL_ID: 'global.anthropic.claude-opus-4-7',
      },
    });

    const ttsFn = new NodejsFunction(this, 'TtsFn', {
      functionName: 'transmeet-tts',
      entry: path.join(__dirname, '../lambda/tts/index.ts'),
      timeout: cdk.Duration.seconds(30),
      ...commonNodejsFunctionProps,
    });

    // ─── REST API ───────────────────────────────────────────────────────────────

    const restApi = new apigateway.RestApi(this, 'RestApi', {
      restApiName: 'transmeet-api',
      deployOptions: {
        stageName: 'prod',
        loggingLevel: apigateway.MethodLoggingLevel.ERROR,
      },
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'Authorization'],
      },
    });

    const meetingsIntegration = new apigateway.LambdaIntegration(meetingsFn);
    const summarizeIntegration = new apigateway.LambdaIntegration(summarizeFn);
    const ttsIntegration = new apigateway.LambdaIntegration(ttsFn);

    // /meetings
    const meetingsResource = restApi.root.addResource('meetings');
    meetingsResource.addMethod('GET', meetingsIntegration);
    meetingsResource.addMethod('POST', meetingsIntegration);

    // /meetings/{id}
    const meetingResource = meetingsResource.addResource('{id}');
    meetingResource.addMethod('GET', meetingsIntegration);
    meetingResource.addMethod('DELETE', meetingsIntegration);

    // /meetings/{id}/summarize
    meetingResource.addResource('summarize').addMethod(
      'POST',
      summarizeIntegration
    );

    // /meetings/{id}/title
    const titleResource = meetingResource.addResource('title');
    titleResource.addMethod('POST', meetingsIntegration);
    titleResource.addMethod('PUT', meetingsIntegration);

    // /tts
    restApi.root.addResource('tts').addMethod('POST', ttsIntegration);

    // ─── S3 + CloudFront (Frontend Hosting) ────────────────────────────────────

    const frontendBucket = new s3.Bucket(this, 'FrontendBucket', {
      bucketName: `transmeet-frontend-${this.account}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const oai = new cloudfront.OriginAccessIdentity(this, 'OAI', {
      comment: 'TransMeet frontend OAI',
    });
    frontendBucket.grantRead(oai);

    // ─── Meeting pages bucket ───────────────────────────────────────────────────

    const pagesBucket = new s3.Bucket(this, 'MeetingPagesBucket', {
      bucketName: `transmeet-meeting-pages-${this.account}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    pagesBucket.grantRead(oai);


    // ─── Route53 + ACM (custom domain) ─────────────────────────────────────────

    const hostedZone = route53.HostedZone.fromLookup(this, 'HostedZone', {
      domainName: ROOT_DOMAIN,
    });

    const certificate = new acm.Certificate(this, 'FrontendCertificate', {
      domainName: APP_DOMAIN,
      validation: acm.CertificateValidation.fromDns(hostedZone),
    });

    const distribution = new cloudfront.Distribution(
      this,
      'FrontendDistribution',
      {
        comment: 'TransMeet frontend',
        domainNames: [APP_DOMAIN],
        certificate,
        defaultBehavior: {
          origin: new origins.S3Origin(frontendBucket, {
            originAccessIdentity: oai,
          }),
          viewerProtocolPolicy:
            cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
        },
        defaultRootObject: 'index.html',
        errorResponses: [
          {
            httpStatus: 404,
            responseHttpStatus: 200,
            responsePagePath: '/index.html',
          },
        ],
        priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      }
    );

    new route53.ARecord(this, 'FrontendAliasRecord', {
      zone: hostedZone,
      recordName: APP_DOMAIN,
      target: route53.RecordTarget.fromAlias(
        new targets.CloudFrontTarget(distribution)
      ),
    });

    // ─── ECR ────────────────────────────────────────────────────────────────────

    const ecrRepo = new ecr.Repository(this, 'WsBackendRepo', {
      repositoryName: 'transmeet-ws-backend',
      lifecycleRules: [{ maxImageCount: 5 }],
    });

    // ─── VPC + ECS Cluster ──────────────────────────────────────────────────────

    const vpc = ec2.Vpc.fromLookup(this, 'DefaultVpc', { isDefault: true });

    const cluster = new ecs.Cluster(this, 'WsCluster', {
      clusterName: 'transmeet-ws-cluster',
      vpc,
    });

    // ─── ECS Task Role ──────────────────────────────────────────────────────────

    const wsTaskRole = new iam.Role(this, 'WsTaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
    });

    wsTaskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['sagemaker:InvokeEndpoint'],
        resources: [
          `arn:aws:sagemaker:${REGION}:*:endpoint/${WHISPER_ENDPOINT}`,
        ],
      })
    );

    wsTaskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
        ],
        resources: [
          `arn:aws:bedrock:*::foundation-model/*`,
          `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
        ],
      })
    );

    wsTaskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['transcribe:StartStreamTranscription'],
        resources: ['*'],
      })
    );

    meetingsTable.grantReadWriteData(wsTaskRole);

    // ─── AgentCore Gateway: Web Search connector ───────────────────────────────

    const gatewayRole = new iam.Role(this, 'WebSearchGatewayRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
    });
    gatewayRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:InvokeGateway'],
        resources: [`arn:aws:bedrock-agentcore:${REGION}:${this.account}:gateway/*`],
      })
    );
    gatewayRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:InvokeWebSearch'],
        resources: [`arn:aws:bedrock-agentcore:${REGION}:aws:tool/web-search.v1`],
      })
    );

    const webSearchGateway = new cdk.CfnResource(this, 'WebSearchGateway', {
      type: 'AWS::BedrockAgentCore::Gateway',
      properties: {
        Name: 'transmeet-web-search',
        Description: 'Web Search Tool connector for the meeting page agent',
        AuthorizerType: 'AWS_IAM',
        ProtocolType: 'MCP',
        RoleArn: gatewayRole.roleArn,
      },
    });
    const webSearchGatewayArn = webSearchGateway.getAtt('GatewayArn').toString();

    const webSearchTarget = new cdk.CfnResource(this, 'WebSearchTarget', {
      type: 'AWS::BedrockAgentCore::GatewayTarget',
      properties: {
        GatewayIdentifier: webSearchGateway.getAtt('GatewayIdentifier').toString(),
        Name: 'web-search-tool',
        TargetConfiguration: {
          Mcp: {
            Connector: {
              Source: { ConnectorId: 'web-search' },
              Configurations: [{ Name: 'WebSearch', ParameterValues: {} }],
            },
          },
        },
        CredentialProviderConfigurations: [
          { CredentialProviderType: 'GATEWAY_IAM_ROLE' },
        ],
      },
    });
    webSearchTarget.addDependency(webSearchGateway);

    // ─── Meeting page agent: container image + Runtime execution role ─────────

    const agentImage = new ecr_assets.DockerImageAsset(this, 'MeetingPageAgentImage', {
      directory: path.join(__dirname, '../../agent'),
      platform: ecr_assets.Platform.LINUX_ARM64,
      exclude: ['.venv', 'evals', 'samples', 'tests', 'scripts', 'harness', '.pytest_cache', '**/__pycache__'],
    });

    const runtimeRole = new iam.Role(this, 'RuntimeExecutionRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
    });
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockModelInvocation',
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
        // 에이전트가 쓰는 모델만 허용: 교차 리전 추론 프로필 + 프로필이 라우팅하는 리전별 기반 모델
        resources: [PAGE_AGENT_MODEL_ID, PAGE_AGENT_FALLBACK_MODEL_ID].flatMap((modelId) => [
          `arn:aws:bedrock:*:${this.account}:inference-profile/${modelId}`,
          `arn:aws:bedrock:*::foundation-model/${modelId.replace(/^us\./, '')}*`,
        ]),
      })
    );
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'EcrImagePull',
        actions: ['ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer', 'ecr:BatchCheckLayerAvailability'],
        resources: [agentImage.repository.repositoryArn],
      })
    );
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'EcrAuth',
        actions: ['ecr:GetAuthorizationToken'],
        resources: ['*'],
      })
    );
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'Observability',
        actions: [
          'xray:PutTraceSegments',
          'xray:PutTelemetryRecords',
          'xray:GetSamplingRules',
          'xray:GetSamplingTargets',
          'logs:CreateLogGroup',
          'logs:CreateLogStream',
          'logs:PutLogEvents',
          'logs:DescribeLogStreams',
          'logs:DescribeLogGroups',
          'logs:PutResourcePolicy',
        ],
        resources: ['*'],
      })
    );
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CloudWatchMetrics',
        actions: ['cloudwatch:PutMetricData'],
        resources: ['*'],
        conditions: { StringEquals: { 'cloudwatch:namespace': 'bedrock-agentcore' } },
      })
    );
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'WorkloadIdentity',
        actions: [
          'bedrock-agentcore:GetWorkloadAccessToken',
          'bedrock-agentcore:GetWorkloadAccessTokenForJWT',
          'bedrock-agentcore:GetWorkloadAccessTokenForUserId',
        ],
        resources: [
          `arn:aws:bedrock-agentcore:${REGION}:${this.account}:workload-identity-directory/default`,
          `arn:aws:bedrock-agentcore:${REGION}:${this.account}:workload-identity-directory/default/workload-identity/${RUNTIME_NAME}-*`,
        ],
      })
    );
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'WebSearchGateway',
        actions: ['bedrock-agentcore:InvokeGateway'],
        resources: [webSearchGatewayArn],
      })
    );
    // 발행은 에이전트가 직접 한다: pages 버킷 쓰기·삭제(이전 페이지 교체) + meeting.page 갱신
    pagesBucket.grantPut(runtimeRole);
    pagesBucket.grantDelete(runtimeRole);
    meetingsTable.grant(runtimeRole, 'dynamodb:UpdateItem');

    // ─── AgentCore Runtime ─────────────────────────────────────────────────────

    const runtime = new bedrockagentcore.CfnRuntime(this, 'MeetingPageRuntime', {
      agentRuntimeName: RUNTIME_NAME,
      description: 'TransMeet meeting page agent (LangGraph)',
      agentRuntimeArtifact: { containerConfiguration: { containerUri: agentImage.imageUri } },
      networkConfiguration: { networkMode: 'PUBLIC' },
      protocolConfiguration: 'HTTP',
      roleArn: runtimeRole.roleArn,
      // 생성은 10~20분. 스트리밍 응답 상한(60분) 안에서 세션은 1시간까지만 산다.
      lifecycleConfiguration: { idleRuntimeSessionTimeout: 600, maxLifetime: 3600 },
      environmentVariables: {
        REGION,
        PAGES_BUCKET: pagesBucket.bucketName,
        PAGES_PUBLIC_BASE_URL: `https://${APP_DOMAIN}/p`,
        MEETINGS_TABLE: meetingsTable.tableName,
        WEB_SEARCH_GATEWAY_URL: webSearchGateway.getAtt('GatewayUrl').toString(),
        PAGE_MAX_BYTES: '1500000',
        GRAPH_BUDGET_S: '2400',
      },
    });
    runtime.addDependency(webSearchTarget);
    runtime.node.addDependency(runtimeRole);
    const runtimeArn = runtime.attrAgentRuntimeArn;

    // ─── Task role: invoke the agent runtime + unpublish pages ────────────────

    wsTaskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:InvokeAgentRuntime'],
        resources: [runtimeArn, `${runtimeArn}/*`],
      })
    );
    pagesBucket.grantDelete(wsTaskRole);

    // ─── ECS Task Definition ────────────────────────────────────────────────────

    const taskDef = new ecs.FargateTaskDefinition(this, 'WsTaskDef', {
      cpu: 512,
      memoryLimitMiB: 1024,
      taskRole: wsTaskRole,
    });

    taskDef.addContainer('ws-backend', {
      image: ecs.ContainerImage.fromEcrRepository(ecrRepo, 'latest'),
      portMappings: [{ containerPort: 8000 }],
      environment: {
        REGION,
        MEETINGS_TABLE,
        WHISPER_ENDPOINT,
        BEDROCK_MODEL_ID,
        PAGE_AGENT_RUNTIME_ARN: runtimeArn,
        PAGE_AGENT_MODEL_ID,
        PAGE_AGENT_FALLBACK_MODEL_ID,
        PAGES_BUCKET: pagesBucket.bucketName,
        PAGES_PUBLIC_BASE_URL: `https://${APP_DOMAIN}/p`,
      },
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'transmeet-ws' }),
    });

    // ─── Security Groups ────────────────────────────────────────────────────────

    const albSg = new ec2.SecurityGroup(this, 'AlbSg', { vpc });
    albSg.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(80));

    const taskSg = new ec2.SecurityGroup(this, 'WsTaskSg', { vpc });
    taskSg.addIngressRule(albSg, ec2.Port.tcp(8000));

    // ─── ALB + Target Group + Listener ─────────────────────────────────────────

    const alb = new elbv2.ApplicationLoadBalancer(this, 'WsAlb', {
      vpc,
      internetFacing: true,
      securityGroup: albSg,
    });

    const targetGroup = new elbv2.ApplicationTargetGroup(this, 'WsTg', {
      vpc,
      port: 8000,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targetType: elbv2.TargetType.IP,
      healthCheck: {
        path: '/health',
        interval: cdk.Duration.seconds(30),
      },
    });

    alb.addListener('WsListener', { port: 80 }).addTargetGroups(
      'WsTgAttach',
      { targetGroups: [targetGroup] }
    );

    // ─── Fargate Service ────────────────────────────────────────────────────────

    const service = new ecs.FargateService(this, 'WsService', {
      cluster,
      taskDefinition: taskDef,
      desiredCount: 1,
      securityGroups: [taskSg],
      assignPublicIp: true,
    });

    service.attachToApplicationTargetGroup(targetGroup);

    // ─── CloudFront /ws behavior → ALB ─────────────────────────────────────────

    const albOrigin = new origins.HttpOrigin(alb.loadBalancerDnsName, {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
    });

    distribution.addBehavior('/ws', albOrigin, {
      allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
      cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
      originRequestPolicy:
        cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
    });

    // ─── CloudFront /p/* behavior → meeting pages bucket ───────────────────────
    // 공유 해제가 즉시 반영되도록 엣지 캐시를 끈다 (페이지는 작고 조회는 드물다).
    // 페이지는 앱과 같은 오리진에서 서빙되므로 CSP sandbox 로 고유 오리진에 격리하고
    // 스크립트·외부 리소스 로드를 모두 막는다 (검증기를 우회한 HTML 에 대한 2차 방어선).
    const pagesHeadersPolicy = new cloudfront.ResponseHeadersPolicy(this, 'MeetingPagesHeadersPolicy', {
      comment: 'TransMeet meeting pages: sandboxed, no scripts or external loads',
      securityHeadersBehavior: {
        contentSecurityPolicy: {
          contentSecurityPolicy:
            "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'",
          override: true,
        },
        contentTypeOptions: { override: true },
      },
    });
    distribution.addBehavior(
      '/p/*',
      new origins.S3Origin(pagesBucket, { originAccessIdentity: oai }),
      {
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
        responseHeadersPolicy: pagesHeadersPolicy,
      }
    );

    // ─── Cognito ────────────────────────────────────────────────────────────────

    const userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: 'transmeet-users',
      selfSignUpEnabled: true,
      signInAliases: { email: true },
      autoVerify: { email: true },
      passwordPolicy: {
        minLength: 8,
        requireLowercase: false,
        requireUppercase: false,
        requireDigits: false,
        requireSymbols: false,
      },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const userPoolClient = userPool.addClient('WebClient', {
      authFlows: { userSrp: true },
      preventUserExistenceErrors: true,
    });

    // ─── Outputs ────────────────────────────────────────────────────────────────

    new cdk.CfnOutput(this, 'WsBackendEcrUri', {
      value: ecrRepo.repositoryUri,
      description: 'ECR repository URI for the WebSocket backend image',
    });

    new cdk.CfnOutput(this, 'WsAlbDns', {
      value: alb.loadBalancerDnsName,
      description: 'ALB DNS name (CloudFront /ws origin)',
    });

    new cdk.CfnOutput(this, 'WsEndpoint', {
      value: `wss://${distribution.distributionDomainName}/ws`,
      description: 'WebSocket endpoint via CloudFront',
    });

    new cdk.CfnOutput(this, 'RestEndpoint', {
      value: restApi.url,
      exportName: 'TransmeetRestEndpoint',
      description: 'REST API endpoint (https://)',
    });

    new cdk.CfnOutput(this, 'FrontendBucketName', {
      value: frontendBucket.bucketName,
      exportName: 'TransmeetFrontendBucket',
      description: 'S3 bucket for frontend deployment',
    });

    new cdk.CfnOutput(this, 'CloudFrontUrl', {
      value: `https://${distribution.distributionDomainName}`,
      exportName: 'TransmeetCloudFrontUrl',
      description: 'CloudFront distribution URL',
    });

    new cdk.CfnOutput(this, 'CloudFrontDistributionId', {
      value: distribution.distributionId,
      exportName: 'TransmeetCloudFrontDistributionId',
      description: 'CloudFront distribution ID (for cache invalidation)',
    });

    new cdk.CfnOutput(this, 'CustomDomainUrl', {
      value: `https://${APP_DOMAIN}`,
      exportName: 'TransmeetCustomDomainUrl',
      description: 'Custom domain URL',
    });

    new cdk.CfnOutput(this, 'MeetingsTableName', {
      value: meetingsTable.tableName,
      exportName: 'TransmeetMeetingsTable',
      description: 'DynamoDB meetings table name',
    });

    new cdk.CfnOutput(this, 'UserPoolId', {
      value: userPool.userPoolId,
      exportName: 'TransmeetUserPoolId',
      description: 'Cognito User Pool ID',
    });

    new cdk.CfnOutput(this, 'UserPoolClientId', {
      value: userPoolClient.userPoolClientId,
      exportName: 'TransmeetUserPoolClientId',
      description: 'Cognito User Pool Client ID',
    });

    new cdk.CfnOutput(this, 'MeetingPageRuntimeArn', {
      value: runtimeArn,
      exportName: 'TransmeetMeetingPageRuntimeArn',
      description: 'AgentCore Runtime ARN for meeting page generation (LangGraph agent)',
    });

    new cdk.CfnOutput(this, 'PagesBucketName', {
      value: pagesBucket.bucketName,
      exportName: 'TransmeetPagesBucket',
      description: 'S3 bucket holding published meeting pages',
    });

    new cdk.CfnOutput(this, 'WebSearchGatewayArn', {
      value: webSearchGatewayArn,
      description: 'AgentCore Gateway ARN (Web Search connector)',
    });
  }
}
