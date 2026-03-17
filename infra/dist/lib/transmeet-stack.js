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
const apigwv2 = __importStar(require("aws-cdk-lib/aws-apigatewayv2"));
const apigwv2_integrations = __importStar(require("aws-cdk-lib/aws-apigatewayv2-integrations"));
const apigateway = __importStar(require("aws-cdk-lib/aws-apigateway"));
const iam = __importStar(require("aws-cdk-lib/aws-iam"));
const s3 = __importStar(require("aws-cdk-lib/aws-s3"));
const cloudfront = __importStar(require("aws-cdk-lib/aws-cloudfront"));
const origins = __importStar(require("aws-cdk-lib/aws-cloudfront-origins"));
const path = __importStar(require("path"));
const WHISPER_ENDPOINT = 'whisper-large-v3-turbo-004709';
const BEDROCK_MODEL_ID = 'global.anthropic.claude-haiku-4-5-20251001-v1:0';
const REGION = 'us-east-1';
class TransmeetStack extends cdk.Stack {
    constructor(scope, id, props) {
        super(scope, id, props);
        // ─── DynamoDB Tables ───────────────────────────────────────────────────────
        const meetingsTable = new dynamodb.Table(this, 'MeetingsTable', {
            tableName: 'transmeet-meetings',
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
        const connectionsTable = new dynamodb.Table(this, 'ConnectionsTable', {
            tableName: 'transmeet-connections',
            partitionKey: { name: 'connectionId', type: dynamodb.AttributeType.STRING },
            billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
            timeToLiveAttribute: 'ttl',
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });
        // ─── IAM Role ───────────────────────────────────────────────────────────────
        const lambdaRole = new iam.Role(this, 'LambdaRole', {
            roleName: 'transmeet-lambda-role',
            assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
            ],
        });
        // SageMaker Whisper endpoint
        lambdaRole.addToPolicy(new iam.PolicyStatement({
            actions: ['sagemaker:InvokeEndpoint'],
            resources: [
                `arn:aws:sagemaker:${REGION}:*:endpoint/${WHISPER_ENDPOINT}`,
            ],
        }));
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
        connectionsTable.grantReadWriteData(lambdaRole);
        // ─── Shared Lambda Config ───────────────────────────────────────────────────
        const commonEnv = {
            MEETINGS_TABLE: meetingsTable.tableName,
            CONNECTIONS_TABLE: connectionsTable.tableName,
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
        // ─── Lambda Functions ───────────────────────────────────────────────────────
        const wsConnectFn = new aws_lambda_nodejs_1.NodejsFunction(this, 'WsConnectFn', {
            functionName: 'transmeet-ws-connect',
            entry: path.join(__dirname, '../lambda/ws-connect/index.ts'),
            timeout: cdk.Duration.seconds(10),
            ...commonNodejsFunctionProps,
        });
        const wsDisconnectFn = new aws_lambda_nodejs_1.NodejsFunction(this, 'WsDisconnectFn', {
            functionName: 'transmeet-ws-disconnect',
            entry: path.join(__dirname, '../lambda/ws-disconnect/index.ts'),
            timeout: cdk.Duration.seconds(10),
            ...commonNodejsFunctionProps,
        });
        const wsAudioFn = new aws_lambda_nodejs_1.NodejsFunction(this, 'WsAudioFn', {
            functionName: 'transmeet-ws-audio',
            entry: path.join(__dirname, '../lambda/ws-audio/index.ts'),
            timeout: cdk.Duration.seconds(30),
            memorySize: 512,
            ...commonNodejsFunctionProps,
        });
        const meetingsFn = new aws_lambda_nodejs_1.NodejsFunction(this, 'MeetingsFn', {
            functionName: 'transmeet-meetings',
            entry: path.join(__dirname, '../lambda/meetings/index.ts'),
            timeout: cdk.Duration.seconds(15),
            ...commonNodejsFunctionProps,
        });
        const summarizeFn = new aws_lambda_nodejs_1.NodejsFunction(this, 'SummarizeFn', {
            functionName: 'transmeet-summarize',
            entry: path.join(__dirname, '../lambda/summarize/index.ts'),
            timeout: cdk.Duration.seconds(60),
            ...commonNodejsFunctionProps,
        });
        const ttsFn = new aws_lambda_nodejs_1.NodejsFunction(this, 'TtsFn', {
            functionName: 'transmeet-tts',
            entry: path.join(__dirname, '../lambda/tts/index.ts'),
            timeout: cdk.Duration.seconds(30),
            ...commonNodejsFunctionProps,
        });
        // ─── WebSocket API ──────────────────────────────────────────────────────────
        const wsApi = new apigwv2.WebSocketApi(this, 'WsApi', {
            apiName: 'transmeet-websocket',
            connectRouteOptions: {
                integration: new apigwv2_integrations.WebSocketLambdaIntegration('WsConnectIntegration', wsConnectFn),
            },
            disconnectRouteOptions: {
                integration: new apigwv2_integrations.WebSocketLambdaIntegration('WsDisconnectIntegration', wsDisconnectFn),
            },
            defaultRouteOptions: {
                integration: new apigwv2_integrations.WebSocketLambdaIntegration('WsDefaultIntegration', wsAudioFn),
            },
        });
        wsApi.addRoute('sendAudio', {
            integration: new apigwv2_integrations.WebSocketLambdaIntegration('WsSendAudioIntegration', wsAudioFn),
        });
        const wsStage = new apigwv2.WebSocketStage(this, 'WsStage', {
            webSocketApi: wsApi,
            stageName: 'prod',
            autoDeploy: true,
        });
        // Allow wsAudioFn to push messages back to connected clients
        wsAudioFn.addToRolePolicy(new iam.PolicyStatement({
            actions: ['execute-api:ManageConnections'],
            resources: [
                `arn:aws:execute-api:${REGION}:*:${wsApi.apiId}/${wsStage.stageName}/POST/@connections/*`,
            ],
        }));
        wsAudioFn.addEnvironment('WS_ENDPOINT', wsStage.callbackUrl);
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
        // ─── Outputs ────────────────────────────────────────────────────────────────
        new cdk.CfnOutput(this, 'WsEndpoint', {
            value: wsStage.url,
            exportName: 'TransmeetWsEndpoint',
            description: 'WebSocket API endpoint (wss://)',
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
