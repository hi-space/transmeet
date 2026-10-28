import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { NodejsFunction } from 'aws-cdk-lib/aws-lambda-nodejs';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as ses from 'aws-cdk-lib/aws-ses';
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
const BEDROCK_MODEL_ID = 'global.anthropic.claude-haiku-5-5';
const REGION = 'us-east-1';
const MEETINGS_TABLE = 'transmeet-meetings';
const SUMMARY_TEMPLATES_TABLE = 'transmeet-summary-templates';
const SUMMARY_SNAPSHOTS_TABLE = 'transmeet-summary-snapshots';
// 발행된 미팅 페이지 기록. 미팅 항목은 발언 전체를 인라인으로 담아 긴 미팅은 400 KB 한도에 닿으므로
// 페이지 기록(수백 바이트)은 미팅 항목에 얹지 않고 별도 테이블에 둔다.
const PAGES_TABLE = 'transmeet-pages';
const ROOT_DOMAIN = 'hi-yoo.com';
const APP_DOMAIN = 'transmeet.hi-yoo.com';
// 미팅 종료 메일 발신. hi-yoo.com 은 SES 도메인 identity(DKIM)로 인증한다.
const MAIL_FROM = 'TransMeet <no-reply@hi-yoo.com>';
const MAIL_FROM_DOMAIN = 'mail.hi-yoo.com';
const PAGE_AGENT_MODEL_ID = 'global.anthropic.claude-opus-5-5';
// Opus 5.5 가 Bedrock 에서 ServiceUnavailable 을 반복하는 동안(2026-10-08 수 시간 관측) 백엔드가 넘어가는 대체 모델.
// 백엔드 config.PAGE_AGENT_FALLBACK_MODEL_ID·agent/runtime/model.py ALLOWED_MODEL_IDS 와 같은 값이어야 한다.
const PAGE_AGENT_FALLBACK_MODEL_ID = 'global.anthropic.claude-opus-5';
// AgentCore Runtime 에 올리는 LangGraph 미팅 페이지 에이전트 (agent/runtime, 이미지는 cdk deploy 가 빌드·푸시)
const RUNTIME_NAME = 'transmeet_meeting_page';

export class TransmeetStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const qwenAsrEndpoint = this.node.tryGetContext('qwenAsrEndpoint') || process.env.QWEN_ASR_ENDPOINT || 'transmeet-qwen3-asr';
    const nemotronAsrEndpoint = this.node.tryGetContext('nemotronAsrEndpoint') || process.env.NEMOTRON_ASR_ENDPOINT || 'transmeet-nemotron-asr';
    const qwenAsrRegion = this.node.tryGetContext('qwenAsrRegion') || process.env.QWEN_ASR_REGION || 'ap-northeast-2';
    const nemotronAsrRegion = this.node.tryGetContext('nemotronAsrRegion') || process.env.NEMOTRON_ASR_REGION || 'ap-northeast-2';
    const nemotronStreamingEndpoint = this.node.tryGetContext('nemotronStreamingEndpoint') || process.env.NEMOTRON_STREAMING_ENDPOINT || 'transmeet-nemotron-streaming';
    const nemotronStreamingRegion = this.node.tryGetContext('nemotronStreamingRegion') || process.env.NEMOTRON_STREAMING_REGION || 'ap-northeast-2';
    const nemotronStreamingEnabled = String(this.node.tryGetContext('nemotronStreamingEnabled') ?? process.env.NEMOTRON_STREAMING_ENABLED ?? 'true') === 'true';
    const qwenAsrComponent = this.node.tryGetContext('qwenAsrComponent') || process.env.QWEN_ASR_INFERENCE_COMPONENT || 'transmeet-qwen3-asr-ic-v2';
    const nemotronAsrComponent = this.node.tryGetContext('nemotronAsrComponent') || process.env.NEMOTRON_ASR_INFERENCE_COMPONENT || 'transmeet-nemotron-asr-ic-v2';
    const wsImageTag = this.node.tryGetContext('wsImageTag') || 'latest';
    const speechEndpoints = [
      { endpoint: WHISPER_ENDPOINT, region: REGION },
      { endpoint: qwenAsrEndpoint, region: qwenAsrRegion },
      { endpoint: nemotronAsrEndpoint, region: nemotronAsrRegion },
    ].filter(({ endpoint }) => Boolean(endpoint));

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

    // 사용자별 목록. 정렬 키를 두지 않는다 — createdAt 이 없는 과거 항목도 인덱스에서 빠지지 않게 하고 정렬은 Lambda 가 한다.
    meetingsTable.addGlobalSecondaryIndex({
      indexName: 'userId-index',
      partitionKey: { name: 'userId', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.INCLUDE,
      nonKeyAttributes: ['title', 'createdAt', 'status', 'messageCount'],
    });

    const pagesTable = new dynamodb.Table(this, 'PagesTable', {
      tableName: PAGES_TABLE,
      partitionKey: { name: 'meetingId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // 내 양식(사용자별 요약 프롬프트). 회의는 고를 때 사본을 갖고 가므로 요약 생성 경로는 이 테이블을 읽지 않는다.
    const summaryTemplatesTable = new dynamodb.Table(this, 'SummaryTemplatesTable', {
      tableName: SUMMARY_TEMPLATES_TABLE,
      partitionKey: { name: 'userId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'templateId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // 회의가 고른 내 양식의 사본(meetingId 키). 회의 항목은 발언 전체를 담아 400 KB 한도에 가까우므로 따로 둔다.
    const summarySnapshotsTable = new dynamodb.Table(this, 'SummarySnapshotsTable', {
      tableName: SUMMARY_SNAPSHOTS_TABLE,
      partitionKey: { name: 'meetingId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // ─── Cognito ────────────────────────────────────────────────────────────────
    // REST Authorizer 와 WebSocket 백엔드(토큰 검증)가 쓰므로 둘보다 먼저 만든다.

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
    pagesTable.grantReadWriteData(lambdaRole);
    summaryTemplatesTable.grantReadWriteData(lambdaRole);
    summarySnapshotsTable.grantReadWriteData(lambdaRole);

    // ─── Shared Lambda Config ───────────────────────────────────────────────────

    const commonEnv: Record<string, string> = {
      MEETINGS_TABLE: meetingsTable.tableName,
      PAGES_TABLE: pagesTable.tableName,
      SUMMARY_TEMPLATES_TABLE: summaryTemplatesTable.tableName,
      SUMMARY_SNAPSHOTS_TABLE: summarySnapshotsTable.tableName,
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
        // 요약 모델. WebSocket 경로(backend config.SUMMARY_MODEL_ID)와 같은 최신 Opus.
        SUMMARIZE_MODEL_ID: 'global.anthropic.claude-opus-5-5',
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

    // 모든 메서드가 Cognito ID 토큰을 요구한다. Lambda 는 claims.sub 로 미팅 소유자를 가린다.
    const authorizer = new apigateway.CognitoUserPoolsAuthorizer(this, 'RestAuthorizer', {
      cognitoUserPools: [userPool],
    });
    const authed: apigateway.MethodOptions = {
      authorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
    };

    // Authorizer 가 거절한 401 에도 CORS 헤더가 있어야 브라우저가 CORS 오류 대신 401 을 본다.
    for (const type of [apigateway.ResponseType.DEFAULT_4XX, apigateway.ResponseType.DEFAULT_5XX]) {
      restApi.addGatewayResponse(`Cors${type.responseType}`, {
        type,
        responseHeaders: { 'Access-Control-Allow-Origin': "'*'" },
      });
    }

    const meetingsIntegration = new apigateway.LambdaIntegration(meetingsFn);
    const summarizeIntegration = new apigateway.LambdaIntegration(summarizeFn);
    const ttsIntegration = new apigateway.LambdaIntegration(ttsFn);

    // /meetings
    const meetingsResource = restApi.root.addResource('meetings');
    meetingsResource.addMethod('GET', meetingsIntegration, authed);
    meetingsResource.addMethod('POST', meetingsIntegration, authed);

    // /meetings/{id}
    const meetingResource = meetingsResource.addResource('{id}');
    meetingResource.addMethod('GET', meetingsIntegration, authed);
    meetingResource.addMethod('DELETE', meetingsIntegration, authed);

    // /meetings/{id}/summarize
    meetingResource.addResource('summarize').addMethod(
      'POST',
      summarizeIntegration,
      authed
    );

    // /meetings/{id}/title
    const titleResource = meetingResource.addResource('title');
    titleResource.addMethod('POST', meetingsIntegration, authed);
    titleResource.addMethod('PUT', meetingsIntegration, authed);

    // /meetings/{id}/summary-style — selection is saved without invoking a model
    meetingResource.addResource('summary-style').addMethod('PUT', meetingsIntegration, authed);

    // /tts
    restApi.root.addResource('tts').addMethod('POST', ttsIntegration, authed);

    // /me/email — 미팅 종료 메일 수신 주소 인증(SES 개별 identity)
    // 전용 역할: SES identity 생성·삭제 권한을 다른 REST Lambda 와 공유하지 않는다.
    const emailRole = new iam.Role(this, 'EmailLambdaRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });
    emailRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['ses:GetEmailIdentity', 'ses:CreateEmailIdentity', 'ses:DeleteEmailIdentity'],
        resources: [`arn:aws:ses:${REGION}:${this.account}:identity/*`],
      })
    );
    // 발신 도메인 identity 는 CDK 만 관리한다 — 코드 실수로 지워지면 메일 발송이 통째로 멈춘다
    emailRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.DENY,
        actions: ['ses:CreateEmailIdentity', 'ses:DeleteEmailIdentity'],
        resources: [
          `arn:aws:ses:${REGION}:${this.account}:identity/${ROOT_DOMAIN}`,
          `arn:aws:ses:${REGION}:${this.account}:identity/*.${ROOT_DOMAIN}`,
        ],
      })
    );
    const emailFn = new NodejsFunction(this, 'EmailFn', {
      functionName: 'transmeet-email',
      entry: path.join(__dirname, '../lambda/email/index.ts'),
      timeout: cdk.Duration.seconds(10),
      ...commonNodejsFunctionProps,
      role: emailRole,
    });
    const emailIntegration = new apigateway.LambdaIntegration(emailFn);
    const meResource = restApi.root.addResource('me');
    const meEmailResource = meResource.addResource('email');
    meEmailResource.addMethod('GET', emailIntegration, authed);
    meEmailResource.addResource('verify').addMethod('POST', emailIntegration, authed);

    // /me/summary-templates — 내 양식 CRUD
    const summaryTemplatesFn = new NodejsFunction(this, 'SummaryTemplatesFn', {
      functionName: 'transmeet-summary-templates',
      entry: path.join(__dirname, '../lambda/summary-templates/index.ts'),
      timeout: cdk.Duration.seconds(10),
      ...commonNodejsFunctionProps,
    });
    const summaryTemplatesIntegration = new apigateway.LambdaIntegration(summaryTemplatesFn);
    const summaryTemplatesResource = meResource.addResource('summary-templates');
    summaryTemplatesResource.addMethod('GET', summaryTemplatesIntegration, authed);
    summaryTemplatesResource.addMethod('POST', summaryTemplatesIntegration, authed);
    const summaryTemplateResource = summaryTemplatesResource.addResource('{templateId}');
    summaryTemplateResource.addMethod('PUT', summaryTemplatesIntegration, authed);
    summaryTemplateResource.addMethod('DELETE', summaryTemplatesIntegration, authed);

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
    // 미팅 삭제(REST DELETE) 때 페이지 기록과 함께 발행된 HTML 도 지운다
    pagesBucket.grantDelete(lambdaRole);
    meetingsFn.addEnvironment('PAGES_BUCKET', pagesBucket.bucketName);

    // ─── Meeting audio bucket ──────────────────────────────────────────────────
    // 녹음 구간(PCM)과 합친 MP3. 페이지 버킷과 달리 CloudFront 로 공개하지 않고 서명 URL 로만 내려준다.
    const audioBucket = new s3.Bucket(this, 'MeetingAudioBucket', {
      bucketName: `transmeet-meeting-audio-${this.account}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    // 미팅 삭제 때 그 미팅의 오디오도 지운다 — 목록·삭제만, 읽기는 주지 않는다
    lambdaRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['s3:ListBucket'],
        resources: [audioBucket.bucketArn],
        conditions: { StringLike: { 's3:prefix': ['meetings/*'] } },
      })
    );
    lambdaRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['s3:DeleteObject'],
        resources: [audioBucket.arnForObjects('meetings/*')],
      })
    );
    meetingsFn.addEnvironment('AUDIO_BUCKET', audioBucket.bucketName);


    // ─── Route53 + ACM (custom domain) ─────────────────────────────────────────

    const hostedZone = route53.HostedZone.fromLookup(this, 'HostedZone', {
      domainName: ROOT_DOMAIN,
    });

    // ─── SES: 발신 도메인 ───────────────────────────────────────────────────────
    // Route53 호스티드 존에 Easy DKIM CNAME 과 MAIL FROM(MX/SPF) 레코드를 CDK 가 만든다.
    // amazon.com 같은 사내 메일은 DKIM·SPF·DMARC 가 맞지 않으면 격리하므로 셋 다 둔다.
    new ses.EmailIdentity(this, 'MailDomainIdentity', {
      identity: ses.Identity.publicHostedZone(hostedZone),
      mailFromDomain: MAIL_FROM_DOMAIN,
    });
    new route53.TxtRecord(this, 'MailDmarcRecord', {
      zone: hostedZone,
      recordName: '_dmarc',
      values: ['v=DMARC1; p=none;'],
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
        actions: ['sagemaker:InvokeEndpoint', 'sagemaker:DescribeEndpoint'],
        resources: speechEndpoints.map(
          ({ endpoint, region }) => `arn:aws:sagemaker:${region}:${this.account}:endpoint/${endpoint}`
        ),
      })
    );
    wsTaskRole.addToPolicy(
      new iam.PolicyStatement({
        // The live HTTP/2 runtime also checks InvokeEndpoint on this same ARN.
        actions: ['sagemaker:InvokeEndpoint', 'sagemaker:InvokeEndpointWithBidirectionalStream', 'sagemaker:DescribeEndpoint'],
        resources: [`arn:aws:sagemaker:${nemotronStreamingRegion}:${this.account}:endpoint/${nemotronStreamingEndpoint}`],
      })
    );
    wsTaskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'sagemaker:DescribeInferenceComponent',
          'sagemaker:UpdateInferenceComponentRuntimeConfig',
        ],
        resources: [
          { component: qwenAsrComponent, region: qwenAsrRegion },
          { component: nemotronAsrComponent, region: nemotronAsrRegion },
        ].map(
          ({ component, region }) => `arn:aws:sagemaker:${region}:${this.account}:inference-component/${component}`
        ),
      })
    );
    wsTaskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['cloudwatch:PutMetricData'],
        resources: ['*'],
        conditions: { StringEquals: { 'cloudwatch:namespace': 'TransMeet/STT' } },
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
    pagesTable.grantReadWriteData(wsTaskRole);
    // 요약은 사본만 읽는다(양식 테이블 권한은 주지 않는다)
    summarySnapshotsTable.grantReadData(wsTaskRole);

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
      exclude: ['.venv', '.page-backup', 'evals', 'samples', 'tests', 'scripts', 'harness', '.pytest_cache', '**/__pycache__'],
    });

    const runtimeRole = new iam.Role(this, 'RuntimeExecutionRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
    });
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockModelInvocation',
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
        // 에이전트가 쓰는 모델만 허용: 교차 리전 추론 프로필(global./us.) + 프로필이 라우팅하는 리전별 기반 모델
        resources: [PAGE_AGENT_MODEL_ID, PAGE_AGENT_FALLBACK_MODEL_ID].flatMap((modelId) => [
          `arn:aws:bedrock:*:${this.account}:inference-profile/${modelId}`,
          `arn:aws:bedrock:*::foundation-model/${modelId.replace(/^(us|global)\./, '')}*`,
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
    // 발행은 에이전트가 직접 한다: pages 버킷 쓰기·삭제(이전 페이지 교체) + 페이지 테이블 기록.
    // 미팅 테이블은 존재 확인(TransactWriteItems 의 ConditionCheck)만 — 미팅 항목은 쓰지 않는다.
    pagesBucket.grantPut(runtimeRole);
    pagesBucket.grantDelete(runtimeRole);
    meetingsTable.grant(runtimeRole, 'dynamodb:ConditionCheckItem');
    pagesTable.grant(runtimeRole, 'dynamodb:GetItem', 'dynamodb:PutItem');

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
        PAGES_TABLE: pagesTable.tableName,
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
    // 녹음 구간 업로드·합치기·서명 URL, 종료 메일 발송
    audioBucket.grantReadWrite(wsTaskRole);
    wsTaskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['ses:SendEmail', 'ses:GetEmailIdentity'],
        resources: [`arn:aws:ses:${REGION}:${this.account}:identity/*`],
      })
    );

    // ─── ECS Task Definition ────────────────────────────────────────────────────

    const taskDef = new ecs.FargateTaskDefinition(this, 'WsTaskDef', {
      cpu: 512,
      memoryLimitMiB: 1024,
      taskRole: wsTaskRole,
    });

    taskDef.addContainer('ws-backend', {
      image: ecs.ContainerImage.fromEcrRepository(ecrRepo, wsImageTag),
      portMappings: [{ containerPort: 8000 }],
      environment: {
        REGION,
        MEETINGS_TABLE,
        PAGES_TABLE,
        SUMMARY_SNAPSHOTS_TABLE,
        WHISPER_ENDPOINT,
        QWEN_ASR_ENDPOINT: qwenAsrEndpoint,
        NEMOTRON_ASR_ENDPOINT: nemotronAsrEndpoint,
        NEMOTRON_STREAMING_ENDPOINT: nemotronStreamingEndpoint,
        NEMOTRON_STREAMING_ENABLED: String(nemotronStreamingEnabled),
        NEMOTRON_STREAMING_CHUNK_MS: process.env.NEMOTRON_STREAMING_CHUNK_MS || '560',
        QWEN_ASR_INFERENCE_COMPONENT: qwenAsrComponent,
        NEMOTRON_ASR_INFERENCE_COMPONENT: nemotronAsrComponent,
        QWEN_ASR_REGION: qwenAsrRegion,
        NEMOTRON_ASR_REGION: nemotronAsrRegion,
        NEMOTRON_STREAMING_REGION: nemotronStreamingRegion,
        STT_IDLE_SECONDS: process.env.STT_IDLE_SECONDS || '0',
        BEDROCK_MODEL_ID,
        PAGE_AGENT_RUNTIME_ARN: runtimeArn,
        PAGE_AGENT_MODEL_ID,
        PAGE_AGENT_FALLBACK_MODEL_ID,
        PAGES_BUCKET: pagesBucket.bucketName,
        PAGES_PUBLIC_BASE_URL: `https://${APP_DOMAIN}/p`,
        // 녹음 구간·MP3 와 미팅 종료 메일 (backend/app/audio_store.py, mailer.py)
        AUDIO_BUCKET: audioBucket.bucketName,
        MAIL_FROM,
        APP_BASE_URL: `https://${APP_DOMAIN}`,
        // WebSocket 연결 시 ID 토큰 검증(발급자·audience)
        COGNITO_USER_POOL_ID: userPool.userPoolId,
        COGNITO_CLIENT_ID: userPoolClient.userPoolClientId,
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
    // CORS: 앱이 다른 오리진(CloudFront 기본 도메인, 로컬 dev 서버)에서 열렸을 때도 페이지 HTML 을
    // fetch 해 마크다운으로 바꿀 수 있게 한다. 페이지는 링크를 아는 사람은 누구나 읽는 공개 자원이고
    // GET/HEAD 만 허용되므로 모든 오리진에 열어도 새로 노출되는 것은 없다.
    const pagesHeadersPolicy = new cloudfront.ResponseHeadersPolicy(this, 'MeetingPagesHeadersPolicy', {
      comment: 'TransMeet meeting pages: sandboxed, no scripts or external loads, CORS read-only',
      corsBehavior: {
        accessControlAllowOrigins: ['*'],
        accessControlAllowMethods: ['GET', 'HEAD'],
        accessControlAllowHeaders: ['*'],
        accessControlAllowCredentials: false,
        accessControlMaxAge: cdk.Duration.hours(1),
        originOverride: true,
      },
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

    new cdk.CfnOutput(this, 'PagesTableName', { value: pagesTable.tableName });
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

    new cdk.CfnOutput(this, 'AudioBucketName', {
      value: audioBucket.bucketName,
      description: 'S3 bucket holding meeting recordings',
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
