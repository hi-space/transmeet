#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$SCRIPT_DIR/infra"
APP_DIR="$SCRIPT_DIR"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[deploy]${NC} $1"; }
warn()  { echo -e "${YELLOW}[deploy]${NC} $1"; }

COMMAND="${1:-all}"

deploy_infra() {
  info "Installing CDK dependencies..."
  cd "$INFRA_DIR" && npm install

  info "Synthesizing CloudFormation template..."
  npx cdk synth

  info "Deploying AWS infrastructure..."
  npx cdk deploy --require-approval never --outputs-file outputs.json

  info "Infrastructure deployed. Outputs saved to infra/outputs.json"
  cat outputs.json
}

deploy_frontend() {
  info "Building Next.js app..."
  cd "$APP_DIR" && npm run build

  # Read CloudFront/S3 outputs
  OUTPUTS_FILE="$INFRA_DIR/outputs.json"
  if [ ! -f "$OUTPUTS_FILE" ]; then
    warn "infra/outputs.json not found. Run './deploy.sh infra' first."
    exit 1
  fi

  BUCKET=$(jq -r '.TransmeetStack.FrontendBucketName' "$OUTPUTS_FILE")
  CF_URL=$(jq -r '.TransmeetStack.CloudFrontUrl' "$OUTPUTS_FILE")
  CF_ID=$(jq -r '.TransmeetStack.CloudFrontDistributionId // empty' "$OUTPUTS_FILE")

  info "Uploading to S3 bucket: $BUCKET"
  aws s3 sync "$APP_DIR/out" "s3://$BUCKET" --delete \
    --cache-control "public, max-age=31536000, immutable" \
    --exclude "*.html"

  aws s3 sync "$APP_DIR/out" "s3://$BUCKET" --delete \
    --cache-control "no-cache" \
    --include "*.html"

  invalidate_cloudfront "$CF_ID"

  info "Frontend deployed: $CF_URL"
}

# 에이전트 스킬을 S3 로 올린다. 하네스는 세션 시작 시 스킬을 다시 받으므로
# 재배포 없이 스킬만 바꿀 수 있다. _evals 아래 평가 산출물은 올리지 않는다.
deploy_skills() {
  OUTPUTS_FILE="$INFRA_DIR/outputs.json"
  if [ ! -f "$OUTPUTS_FILE" ]; then
    warn "infra/outputs.json not found. Run './deploy.sh infra' first."
    exit 1
  fi
  BUCKET=$(jq -r '.TransmeetStack.SkillsBucketName // empty' "$OUTPUTS_FILE")
  if [ -z "$BUCKET" ]; then
    warn "SkillsBucketName not in outputs.json — run './deploy.sh infra' to create the bucket."
    exit 1
  fi

  info "Validating skills..."
  for skill in "$APP_DIR"/agent/skills/*/; do
    [ -f "$skill/SKILL.md" ] || { warn "Missing SKILL.md in $skill"; exit 1; }
  done

  info "Uploading skills to s3://$BUCKET/skills/"
  aws s3 sync "$APP_DIR/agent/skills/" "s3://$BUCKET/skills/" --delete \
    --exclude "*/__pycache__/*" --exclude "_evals/*" --exclude ".DS_Store"
  info "Skills deployed."
}

# HTML/서비스워커는 no-cache 로 올라가지만 엣지 캐시가 남을 수 있어 명시적으로 무효화한다.
# 해시가 붙은 /_next/static/* 는 immutable 이므로 무효화 대상에서 제외한다.
invalidate_cloudfront() {
  local cf_id="$1"

  if [ -z "$cf_id" ]; then
    warn "CloudFrontDistributionId not in outputs.json — skipping invalidation."
    warn "Run './deploy.sh infra' to add it, or invalidate manually."
    return 0
  fi

  info "Invalidating CloudFront cache: $cf_id"
  local invalidation_id
  invalidation_id=$(aws cloudfront create-invalidation \
    --distribution-id "$cf_id" \
    --paths "/" "/index.html" "/404.html" "/index.txt" "/sw.js" "/manifest.json" \
    --query 'Invalidation.Id' --output text)

  info "Waiting for invalidation $invalidation_id to complete..."
  aws cloudfront wait invalidation-completed \
    --distribution-id "$cf_id" --id "$invalidation_id"

  info "Invalidation complete."
}

case "$COMMAND" in
  infra)    deploy_infra ;;
  skills)   deploy_skills ;;
  frontend) deploy_frontend ;;
  all)
    deploy_infra
    deploy_skills
    deploy_frontend
    ;;
  diff)
    cd "$INFRA_DIR" && npm install --silent && npx cdk diff
    ;;
  destroy)
    warn "Destroying infrastructure..."
    cd "$INFRA_DIR" && npx cdk destroy
    ;;
  *)
    echo "Usage: $0 [infra|skills|frontend|all|diff|destroy]"
    exit 1
    ;;
esac

info "Done!"
