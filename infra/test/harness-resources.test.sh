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
check 'AWS::BedrockAgentCore::Harness' "x.Properties.HarnessName==='transmeet_meeting_page' && x.Properties.Model.BedrockModelConfig.ModelId==='us.anthropic.claude-opus-5-5' && x.Properties.Model.BedrockModelConfig.MaxTokens===64000 && x.Properties.Memory.Disabled!==undefined && x.Properties.Tools.length===3 && x.Properties.Skills.length===4 && x.Properties.MaxIterations===80 && x.Properties.TimeoutSeconds===1800"
check 'AWS::BedrockAgentCore::Harness' "x.Properties.Tools.some(t=>t.Type==='inline_function' && t.Name==='publish_page' && t.Config.InlineFunction.InputSchema.required.includes('html'))"
check 'AWS::BedrockAgentCore::Harness' "x.Properties.SystemPrompt[0].Text.includes('publish_page')"
check 'AWS::BedrockAgentCore::Gateway' "x.Properties.AuthorizerType==='AWS_IAM' && x.Properties.ProtocolType==='MCP'"
check 'AWS::BedrockAgentCore::GatewayTarget' "x.Properties.TargetConfiguration.Mcp.Connector.Source.ConnectorId==='web-search' && x.Properties.CredentialProviderConfigurations[0].CredentialProviderType==='GATEWAY_IAM_ROLE'"
check 'AWS::S3::Bucket' "JSON.stringify(x.Properties.BucketName).includes('transmeet-meeting-pages')"
check 'AWS::S3::Bucket' "JSON.stringify(x.Properties.BucketName).includes('transmeet-agent-skills')"
check 'AWS::CloudFront::Distribution' "x.Properties.DistributionConfig.CacheBehaviors.some(b=>b.PathPattern==='/p/*')"
check 'AWS::IAM::Policy' "JSON.stringify(x.Properties.PolicyDocument).includes('bedrock-agentcore:InvokeHarness')"
# 대체 하네스: 모델만 다르고 프롬프트·도구·스킬은 같다. 태스크 역할은 둘 다 호출할 수 있어야 한다.
check 'AWS::BedrockAgentCore::Harness' "x.Properties.HarnessName==='transmeet_meeting_page_fallback' && x.Properties.Model.BedrockModelConfig.ModelId==='us.anthropic.claude-opus-5' && x.Properties.Tools.length===3 && x.Properties.Skills.length===4 && x.Properties.SystemPrompt[0].Text.includes('publish_page')"
check 'AWS::IAM::Policy' "(()=>{const st=x.Properties.PolicyDocument.Statement.find(s=>JSON.stringify(s.Action).includes('bedrock-agentcore:InvokeHarness')); return !!st && JSON.stringify(st.Resource).includes('MeetingPageHarnessFallback')})()"
check 'AWS::ECS::TaskDefinition' "JSON.stringify(x.Properties.ContainerDefinitions[0].Environment).includes('HARNESS_FALLBACK_ARN')"
check 'AWS::IAM::Policy' "JSON.stringify(x.Properties.PolicyDocument).includes('bedrock-agentcore:InvokeWebSearch')"
check 'AWS::ECS::TaskDefinition' "JSON.stringify(x.Properties.ContainerDefinitions[0].Environment).includes('HARNESS_ARN')"
# 발행 페이지는 앱과 같은 오리진에서 서빙되므로 CSP sandbox 로 스크립트·외부 로드를 막는다
check 'AWS::CloudFront::Distribution' "x.Properties.DistributionConfig.CacheBehaviors.some(b=>b.PathPattern==='/p/*' && b.ResponseHeadersPolicyId)"
check 'AWS::CloudFront::ResponseHeadersPolicy' "(()=>{const c=x.Properties.ResponseHeadersPolicyConfig.SecurityHeadersConfig; const p=c.ContentSecurityPolicy.ContentSecurityPolicy; return /(^|;)\\s*sandbox\\s*(;|$)/.test(p) && p.includes(\"default-src 'none'\") && p.includes(\"form-action 'none'\") && c.ContentTypeOptions.Override===true})()"
# 하네스 역할은 지정 모델만 호출할 수 있어야 한다 (foundation-model/* 와일드카드 금지)
check 'AWS::IAM::Policy' "(()=>{const st=x.Properties.PolicyDocument.Statement.find(s=>s.Sid==='BedrockModelInvocation' && JSON.stringify(x.Properties.Roles).includes('HarnessExecutionRole')); if(!st) return false; const r=JSON.stringify(st.Resource); return !r.includes('foundation-model/*') && !r.includes('inference-profile/*') && r.includes('inference-profile/us.anthropic.claude-opus-5-5') && r.includes('foundation-model/anthropic.claude-opus-5-5*') && r.includes('inference-profile/us.anthropic.claude-opus-5\"')})()"
echo "all harness resource checks passed"
