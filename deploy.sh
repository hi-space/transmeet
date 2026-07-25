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
  frontend) deploy_frontend ;;
  all)
    deploy_infra
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
    echo "Usage: $0 [infra|frontend|all|diff|destroy]"
    exit 1
    ;;
esac

info "Done!"
