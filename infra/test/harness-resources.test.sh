#!/bin/bash
# cdk synth 결과에 하네스 관련 리소스가 기대한 형태로 들어갔는지 확인한다.
set -euo pipefail
cd "$(dirname "$0")/.."
npx cdk synth --quiet > /dev/null
T=cdk.out/TransmeetStack.template.json
check() { node -e "
const t=require('./$T'); const r=Object.values(t.Resources);
const found=r.some(x=>x.Type==='$1' && ($2));
if(!found){console.error('MISSING: $1'); process.exit(1)} console.log('ok  $1')"; }
check 'AWS::BedrockAgentCore::Harness' "x.Properties.HarnessName==='transmeet_meeting_page' && x.Properties.Model.BedrockModelConfig.ModelId==='global.anthropic.claude-opus-5-5' && x.Properties.Memory.Disabled!==undefined && x.Properties.Tools.length===3 && x.Properties.Skills.length===4 && x.Properties.MaxIterations===80 && x.Properties.TimeoutSeconds===900"
check 'AWS::BedrockAgentCore::Harness' "x.Properties.Tools.some(t=>t.Type==='inline_function' && t.Name==='publish_page' && t.Config.InlineFunction.InputSchema.required.includes('html'))"
check 'AWS::BedrockAgentCore::Harness' "x.Properties.SystemPrompt[0].Text.includes('publish_page')"
check 'AWS::BedrockAgentCore::Gateway' "x.Properties.AuthorizerType==='AWS_IAM' && x.Properties.ProtocolType==='MCP'"
check 'AWS::BedrockAgentCore::GatewayTarget' "x.Properties.TargetConfiguration.Mcp.Connector.Source.ConnectorId==='web-search' && x.Properties.CredentialProviderConfigurations[0].CredentialProviderType==='GATEWAY_IAM_ROLE'"
check 'AWS::S3::Bucket' "JSON.stringify(x.Properties.BucketName).includes('transmeet-meeting-pages')"
check 'AWS::S3::Bucket' "JSON.stringify(x.Properties.BucketName).includes('transmeet-agent-skills')"
check 'AWS::CloudFront::Distribution' "x.Properties.DistributionConfig.CacheBehaviors.some(b=>b.PathPattern==='/p/*')"
check 'AWS::IAM::Policy' "JSON.stringify(x.Properties.PolicyDocument).includes('bedrock-agentcore:InvokeHarness')"
check 'AWS::IAM::Policy' "JSON.stringify(x.Properties.PolicyDocument).includes('bedrock-agentcore:InvokeWebSearch')"
check 'AWS::ECS::TaskDefinition' "JSON.stringify(x.Properties.ContainerDefinitions[0].Environment).includes('HARNESS_ARN')"
echo "all harness resource checks passed"
