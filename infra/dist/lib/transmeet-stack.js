"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || function (mod) {
    if (mod && mod.__esModule) return mod;
    var result = {};
    if (mod != null) for (var k in mod) if (k !== "default" && Object.prototype.hasOwnProperty.call(mod, k)) __createBinding(result, mod, k);
    __setModuleDefault(result, mod);
    return result;
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.TransmeetStack = void 0;
const cdk = __importStar(require("aws-cdk-lib"));
const dynamodb = __importStar(require("aws-cdk-lib/aws-dynamodb"));
const lambda = __importStar(require("aws-cdk-lib/aws-lambda"));
const aws_lambda_nodejs_1 = require("aws-cdk-lib/aws-lambda-nodejs");
const apigateway = __importStar(require("aws-cdk-lib/aws-apigateway"));
const iam = __importStar(require("aws-cdk-lib/aws-iam"));
const s3 = __importStar(require("aws-cdk-lib/aws-s3"));
const cloudfront = __importStar(require("aws-cdk-lib/aws-cloudfront"));
const origins = __importStar(require("aws-cdk-lib/aws-cloudfront-origins"));
const ecr = __importStar(require("aws-cdk-lib/aws-ecr"));
const ecs = __importStar(require("aws-cdk-lib/aws-ecs"));
const ec2 = __importStar(require("aws-cdk-lib/aws-ec2"));
const elbv2 = __importStar(require("aws-cdk-lib/aws-elasticloadbalancingv2"));
const path = __importStar(require("path"));
const WHISPER_ENDPOINT = 'whisper-large';
const BEDROCK_MODEL_ID = 'global.anthropic.claude-haiku-4-5-20251001-v1:0';
const REGION = 'us-east-1';
const MEETINGS_TABLE = 'transmeet-meetings';
class TransmeetStack extends cdk.Stack {
    constructor(scope, id, props) {
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
                iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
            ],
        });
        // Bedrock — allow both foundation models and cross-region inference profiles (global.*)
        lambdaRole.addToPolicy(new iam.PolicyStatement({
            actions: [
                'bedrock:InvokeModel',
                'bedrock:InvokeModelWithResponseStream',
            ],
            resources: [
                `arn:aws:bedrock:*::foundation-model/*`,
                `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
            ],
        }));
        // Polly
        lambdaRole.addToPolicy(new iam.PolicyStatement({
            actions: ['polly:SynthesizeSpeech'],
            resources: ['*'],
        }));
        // DynamoDB
        meetingsTable.grantReadWriteData(lambdaRole);
        // ─── Shared Lambda Config ───────────────────────────────────────────────────
        const commonEnv = {
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
        const meetingsFn = new aws_lambda_nodejs_1.NodejsFunction(this, 'MeetingsFn', {
            functionName: 'transmeet-meetings',
            entry: path.join(__dirname, '../lambda/meetings/index.ts'),
            timeout: cdk.Duration.seconds(15),
            ...commonNodejsFunctionProps,
        });
        const summarizeFn = new aws_lambda_nodejs_1.NodejsFunction(this, 'SummarizeFn', {
            functionName: 'transmeet-summarize',
            entry: path.join(__dirname, '../lambda/summarize/index.ts'),
            timeout: cdk.Duration.seconds(120),
            ...commonNodejsFunctionProps,
            environment: {
                ...commonEnv,
                // Sonnet for higher-quality meeting summaries
                SUMMARIZE_MODEL_ID: 'global.anthropic.claude-sonnet-4-6',
            },
        });
        const ttsFn = new aws_lambda_nodejs_1.NodejsFunction(this, 'TtsFn', {
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
        meetingResource.addResource('summarize').addMethod('POST', summarizeIntegration);
        // /meetings/{id}/title
        meetingResource.addResource('title').addMethod('POST', meetingsIntegration);
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
        const distribution = new cloudfront.Distribution(this, 'FrontendDistribution', {
            comment: 'TransMeet frontend',
            defaultBehavior: {
                origin: new origins.S3Origin(frontendBucket, {
                    originAccessIdentity: oai,
                }),
                viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
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
        wsTaskRole.addToPolicy(new iam.PolicyStatement({
            actions: ['sagemaker:InvokeEndpoint'],
            resources: [
                `arn:aws:sagemaker:${REGION}:*:endpoint/${WHISPER_ENDPOINT}`,
            ],
        }));
        wsTaskRole.addToPolicy(new iam.PolicyStatement({
            actions: [
                'bedrock:InvokeModel',
                'bedrock:InvokeModelWithResponseStream',
            ],
            resources: [
                `arn:aws:bedrock:*::foundation-model/*`,
                `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
            ],
        }));
        meetingsTable.grantReadWriteData(wsTaskRole);
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
        alb.addListener('WsListener', { port: 80 }).addTargetGroups('WsTgAttach', { targetGroups: [targetGroup] });
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
            originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
            viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
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
        new cdk.CfnOutput(this, 'MeetingsTableName', {
            value: meetingsTable.tableName,
            exportName: 'TransmeetMeetingsTable',
            description: 'DynamoDB meetings table name',
        });
    }
}
exports.TransmeetStack = TransmeetStack;
