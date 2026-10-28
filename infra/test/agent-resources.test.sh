#!/bin/bash
# cdk synth 결과에 미팅 페이지 에이전트(AgentCore Runtime) 관련 리소스가 기대한 형태로 들어갔는지 확인한다.
set -euo pipefail
cd "$(dirname "$0")/.."
npx cdk synth --quiet > /dev/null
T=cdk.out/TransmeetStack.template.json
check() { node -e "
const t=require('./$T'); const r=Object.values(t.Resources);
const found=r.some(x=>x.Type==='$1' && ($2));
if(!found){console.error('MISSING: $1'); process.exit(1)} console.log('ok  $1')"; }
absent() { node -e "
const t=require('./$T'); const r=Object.values(t.Resources);
if(r.some(x=>x.Type==='$1')){console.error('UNEXPECTED: $1'); process.exit(1)} console.log('ok  no $1')"; }

# Runtime: 이름·네트워크·프로토콜·수명·환경 변수
check 'AWS::BedrockAgentCore::Runtime' "x.Properties.AgentRuntimeName==='transmeet_meeting_page' && x.Properties.NetworkConfiguration.NetworkMode==='PUBLIC' && x.Properties.ProtocolConfiguration==='HTTP' && x.Properties.LifecycleConfiguration.IdleRuntimeSessionTimeout===600 && x.Properties.LifecycleConfiguration.MaxLifetime===3600"
check 'AWS::BedrockAgentCore::Runtime' "(()=>{const e=x.Properties.EnvironmentVariables; return ['PAGES_BUCKET','PAGES_PUBLIC_BASE_URL','MEETINGS_TABLE','PAGES_TABLE','WEB_SEARCH_GATEWAY_URL','PAGE_MAX_BYTES','GRAPH_BUDGET_S','REGION'].every(k=>k in e)})()"
# 페이지 테이블: 미팅 항목(400 KB 한도)과 분리
check 'AWS::DynamoDB::Table' "x.Properties.TableName==='transmeet-pages' && x.Properties.KeySchema.length===1 && x.Properties.KeySchema[0].AttributeName==='meetingId'"
check 'AWS::BedrockAgentCore::Runtime' "JSON.stringify(x.Properties.AgentRuntimeArtifact.ContainerConfiguration.ContainerUri).includes('container-assets')"
# 하네스·스킬 버킷은 더 없다
absent 'AWS::BedrockAgentCore::Harness'
node -e "const t=require('./$T'); if(Object.values(t.Resources).some(x=>x.Type==='AWS::S3::Bucket' && JSON.stringify(x.Properties.BucketName||'').includes('transmeet-agent-skills'))){console.error('UNEXPECTED skills bucket');process.exit(1)} console.log('ok  no skills bucket')"
# Gateway 는 유지
check 'AWS::BedrockAgentCore::Gateway' "x.Properties.AuthorizerType==='AWS_IAM' && x.Properties.ProtocolType==='MCP'"
check 'AWS::BedrockAgentCore::GatewayTarget' "x.Properties.TargetConfiguration.Mcp.Connector.Source.ConnectorId==='web-search' && x.Properties.CredentialProviderConfigurations[0].CredentialProviderType==='GATEWAY_IAM_ROLE'"
check 'AWS::IAM::Policy' "JSON.stringify(x.Properties.PolicyDocument).includes('bedrock-agentcore:InvokeWebSearch')"
# 실행 역할: 지정 모델만(와일드카드 금지), Gateway 호출, pages 버킷 쓰기, 페이지 테이블 Get/Put, 미팅 테이블은 ConditionCheck 만(UpdateItem 없음)
check 'AWS::IAM::Policy' "(()=>{if(!JSON.stringify(x.Properties.Roles).includes('RuntimeExecutionRole')) return false; const st=x.Properties.PolicyDocument.Statement.find(s=>s.Sid==='BedrockModelInvocation'); if(!st) return false; const r=JSON.stringify(st.Resource); return !r.includes('foundation-model/*') && !r.includes('inference-profile/*') && r.includes('inference-profile/global.anthropic.claude-opus-5-5') && r.includes('foundation-model/anthropic.claude-opus-5-5*') && r.includes('inference-profile/global.anthropic.claude-opus-5\"')})()"
check 'AWS::IAM::Policy' "JSON.stringify(x.Properties.Roles).includes('RuntimeExecutionRole') && x.Properties.PolicyDocument.Statement.some(s=>JSON.stringify(s.Action).includes('bedrock-agentcore:InvokeGateway'))"
check 'AWS::IAM::Policy' "JSON.stringify(x.Properties.Roles).includes('RuntimeExecutionRole') && x.Properties.PolicyDocument.Statement.some(s=>JSON.stringify(s.Action).includes('s3:PutObject')) && x.Properties.PolicyDocument.Statement.some(s=>JSON.stringify(s.Action).includes('s3:DeleteObject'))"
check 'AWS::IAM::Policy' "JSON.stringify(x.Properties.Roles).includes('RuntimeExecutionRole') && x.Properties.PolicyDocument.Statement.some(s=>JSON.stringify(s.Action).includes('dynamodb:ConditionCheckItem') && JSON.stringify(s.Resource).includes('MeetingsTable'))"
check 'AWS::IAM::Policy' "JSON.stringify(x.Properties.Roles).includes('RuntimeExecutionRole') && x.Properties.PolicyDocument.Statement.some(s=>JSON.stringify(s.Action).includes('dynamodb:PutItem') && JSON.stringify(s.Action).includes('dynamodb:GetItem') && JSON.stringify(s.Resource).includes('PagesTable'))"
node -e "const t=require('./$T'); const bad=Object.values(t.Resources).some(x=>x.Type==='AWS::IAM::Policy' && JSON.stringify(x.Properties.Roles).includes('RuntimeExecutionRole') && x.Properties.PolicyDocument.Statement.some(s=>JSON.stringify(s.Action).includes('dynamodb:UpdateItem'))); if(bad){console.error('UNEXPECTED runtime role UpdateItem');process.exit(1)} console.log('ok  runtime role has no UpdateItem')"
# REST Lambda: 미팅 삭제 때 페이지 HTML 도 지운다
check 'AWS::IAM::Policy' "JSON.stringify(x.Properties.Roles).includes('LambdaRole') && x.Properties.PolicyDocument.Statement.some(s=>JSON.stringify(s.Action).includes('s3:DeleteObject'))"
check 'AWS::Lambda::Function' "x.Properties.FunctionName==='transmeet-meetings' && 'PAGES_TABLE' in x.Properties.Environment.Variables && 'PAGES_BUCKET' in x.Properties.Environment.Variables"
check 'AWS::IAM::Policy' "JSON.stringify(x.Properties.Roles).includes('RuntimeExecutionRole') && x.Properties.PolicyDocument.Statement.some(s=>JSON.stringify(s.Action).includes('ecr:BatchGetImage'))"
check 'AWS::IAM::Role' "JSON.stringify(x.Properties.AssumeRolePolicyDocument).includes('bedrock-agentcore.amazonaws.com')"
# 태스크 역할: InvokeAgentRuntime 만 (InvokeHarness 없음), PutObject 는 오디오 버킷에만 허용
check 'AWS::IAM::Policy' "JSON.stringify(x.Properties.Roles).includes('WsTaskRole') && x.Properties.PolicyDocument.Statement.some(s=>JSON.stringify(s.Action).includes('bedrock-agentcore:InvokeAgentRuntime') && JSON.stringify(s.Resource).includes('MeetingPageRuntime'))"
node -e "const t=require('./$T'); const bad=Object.values(t.Resources).some(x=>x.Type==='AWS::IAM::Policy' && JSON.stringify(x.Properties.PolicyDocument).includes('InvokeHarness')); if(bad){console.error('UNEXPECTED InvokeHarness');process.exit(1)} console.log('ok  no InvokeHarness')"
node - "$T" <<'NODE'
const t = require('./' + process.argv[2]);
const resources = Object.entries(t.Resources);
const audioBuckets = resources.filter(([, x]) =>
  x.Type === 'AWS::S3::Bucket' &&
  JSON.stringify(x.Properties.BucketName || '').includes('transmeet-meeting-audio'));
if (audioBuckets.length !== 1) {
  console.error('MISSING or ambiguous audio bucket');
  process.exit(1);
}
const audioArn = { 'Fn::GetAtt': [audioBuckets[0][0], 'Arn'] };
const allowed = new Set([
  JSON.stringify(audioArn),
  JSON.stringify({ 'Fn::Join': ['', [audioArn, '/*']] }),
]);
const writes = resources
  .filter(([, x]) => x.Type === 'AWS::IAM::Policy' &&
    JSON.stringify(x.Properties.Roles).includes('WsTaskRole'))
  .flatMap(([, x]) => x.Properties.PolicyDocument.Statement)
  .filter(s => JSON.stringify(s.Action).includes('s3:PutObject'));
if (!writes.length || writes.some(s =>
  !s.Resource || [].concat(s.Resource).some(r => !allowed.has(JSON.stringify(r))))) {
  console.error('MISSING audio write permission or UNEXPECTED task role PutObject outside audio bucket');
  process.exit(1);
}
console.log('ok  task role PutObject is restricted to the audio bucket');
NODE
# ECS 환경 변수
check 'AWS::ECS::TaskDefinition' "(()=>{const e=JSON.stringify(x.Properties.ContainerDefinitions[0].Environment); return e.includes('PAGE_AGENT_RUNTIME_ARN') && e.includes('PAGE_AGENT_MODEL_ID') && e.includes('PAGE_AGENT_FALLBACK_MODEL_ID') && e.includes('PAGES_TABLE') && !e.includes('HARNESS_ARN')})()"
# 발행 페이지 서빙·CSP 는 그대로
check 'AWS::S3::Bucket' "JSON.stringify(x.Properties.BucketName).includes('transmeet-meeting-pages')"
check 'AWS::CloudFront::Distribution' "x.Properties.DistributionConfig.CacheBehaviors.some(b=>b.PathPattern==='/p/*' && b.ResponseHeadersPolicyId)"
check 'AWS::CloudFront::ResponseHeadersPolicy' "(()=>{const c=x.Properties.ResponseHeadersPolicyConfig.SecurityHeadersConfig; const p=c.ContentSecurityPolicy.ContentSecurityPolicy; return /(^|;)\\s*sandbox\\s*(;|$)/.test(p) && p.includes(\"default-src 'none'\") && p.includes(\"form-action 'none'\") && c.ContentTypeOptions.Override===true})()"
echo "all agent resource checks passed"
