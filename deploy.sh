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

  info "Uploading to S3 bucket: $BUCKET"
  aws s3 sync "$APP_DIR/out" "s3://$BUCKET" --delete \
    --cache-control "public, max-age=31536000, immutable" \
    --exclude "*.html"

  aws s3 sync "$APP_DIR/out" "s3://$BUCKET" --delete \
    --cache-control "no-cache" \
    --include "*.html"

  info "Frontend deployed: $CF_URL"
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
