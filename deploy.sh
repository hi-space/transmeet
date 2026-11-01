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

die()   { echo -e "\033[0;31m[deploy]\033[0m $1" >&2; exit 1; }

# 페이지 에이전트 이미지는 이 체크아웃의 agent/ 를 빌드한다. 오래된 체크아웃이나 커밋 안 된 작업이
# 운영에 올라가지 않도록 origin/main 최신 커밋 + 깨끗한 agent/·infra/ 에서만 배포한다.
# 급할 때만 ALLOW_STALE=1 로 건너뛴다.
check_latest_main() {
  [ "${ALLOW_STALE:-0}" = "1" ] && { warn "ALLOW_STALE=1 — 최신 main 확인을 건너뜁니다."; return; }
  cd "$SCRIPT_DIR"
  local branch; branch="$(git rev-parse --abbrev-ref HEAD)"
  [ "$branch" = "main" ] || die "main 이 아닌 '$branch' 에서는 배포하지 않습니다."
  git fetch -q origin main
  local head remote; head="$(git rev-parse HEAD)"; remote="$(git rev-parse origin/main)"
  if [ "$head" != "$remote" ]; then
    git merge-base --is-ancestor "$remote" "$head" \
      && die "origin/main 에 없는 커밋이 있습니다. 먼저 push 하세요." \
      || die "origin/main 보다 뒤처졌습니다. 먼저 git pull 하세요."
  fi
  local dirty; dirty="$(git status --porcelain -- agent infra)"
  [ -z "$dirty" ] || die "agent/·infra/ 에 커밋 안 된 변경이 있습니다:\n$dirty"
  info "배포 기준: main $(git rev-parse --short HEAD)"
}

# deploy.sh 는 백엔드 이미지를 빌드하지 않는다. wsImageTag 기본값(latest)으로 배포하면 예전 이미지로
# 돌아가므로, WS_IMAGE_TAG 를 주지 않으면 지금 서비스에서 돌고 있는 태그를 그대로 쓴다.
running_ws_tag() {
  local svc td
  svc="$(aws ecs list-services --cluster transmeet-ws-cluster --query 'serviceArns[0]' --output text 2>/dev/null)" || return 0
  [ -n "$svc" ] && [ "$svc" != "None" ] || return 0
  td="$(aws ecs describe-services --cluster transmeet-ws-cluster --services "$svc" --query 'services[0].taskDefinition' --output text)"
  aws ecs describe-task-definition --task-definition "$td" \
    --query 'taskDefinition.containerDefinitions[0].image' --output text | sed -n 's/.*:\([^:@]*\)$/\1/p'
}

deploy_infra() {
  check_latest_main
  local ws_tag="${WS_IMAGE_TAG:-$(running_ws_tag)}"
  local ctx=()
  [ -n "$ws_tag" ] && { ctx+=(-c "wsImageTag=$ws_tag"); info "백엔드 이미지 태그: $ws_tag"; }

  info "Installing CDK dependencies..."
  cd "$INFRA_DIR" && npm install

  info "Synthesizing CloudFormation template (builds the agent image, linux/arm64)..."
  npx cdk synth "${ctx[@]}"

  info "Deploying AWS infrastructure..."
  npx cdk deploy --require-approval never --outputs-file outputs.json "${ctx[@]}"

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
  CF_URL=$(jq -r '.TransmeetStack.CustomDomainUrl // .TransmeetStack.CloudFrontUrl' "$OUTPUTS_FILE")
  CF_ID=$(jq -r '.TransmeetStack.CloudFrontDistributionId // empty' "$OUTPUTS_FILE")

  info "Uploading to S3 bucket: $BUCKET"
  # Keep previous hashed assets available to browsers using the previous release.
  aws s3 sync "$APP_DIR/out" "s3://$BUCKET" \
    --cache-control "public, max-age=31536000, immutable" \
    --exclude "*" --include "_next/static/*" --only-show-errors

  # Copy mutable files on every deployment so unchanged service workers and
  # manifests also receive the intended cache metadata.
  aws s3 cp "$APP_DIR/out" "s3://$BUCKET" --recursive \
    --cache-control "no-cache" \
    --exclude "_next/static/*" --only-show-errors

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
    --paths "/" "/index.html" "/404.html" "/404/*" "/index.txt" "/sw.js" "/manifest.json" "/icons/*" "/brand/*" "/workbox-*" "/swe-worker-*" \
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
    ws_tag="${WS_IMAGE_TAG:-$(running_ws_tag)}"
    cd "$INFRA_DIR" && npm install --silent && npx cdk diff ${ws_tag:+-c wsImageTag=$ws_tag}
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
