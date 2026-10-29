#!/usr/bin/env bash
# TransMeet 공유 문서를 https://archive.hi-yoo.com/transmeet/ 아래에 올린다.
#   /transmeet/guide/                                     사용 가이드 (docs/guide)
#   /transmeet/stt-benchmark/                             서울 STT 스트리밍 비교 리포트 (benchmarks/stt-seoul-streaming-2026-10-29)
# 버킷·CloudFront 는 TransMeet CDK 스택과 별개인 공용 아카이브(Project=hi-yoo-archive 태그)다.
set -euo pipefail

BUCKET=hi-yoo-archive-913524902871
PREFIX=transmeet
DISTRIBUTION=E1IO73B4D4SIA3
DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
HTML=(--content-type "text/html; charset=utf-8" --cache-control "no-cache")

# 스크린샷은 하루만 캐시하고, HTML 은 매번 새로 확인하게 한다
aws s3 sync "$DIR/images" "s3://$BUCKET/$PREFIX/guide/images" --delete \
  --cache-control "public, max-age=86400" --only-show-errors
aws s3 cp "$DIR/index.html" "s3://$BUCKET/$PREFIX/guide/index.html" "${HTML[@]}" --only-show-errors

aws s3 cp "$REPO/benchmarks/stt-seoul-streaming-2026-10-29/report.html" "s3://$BUCKET/$PREFIX/stt-benchmark/index.html" \
  "${HTML[@]}" --only-show-errors

# /transmeet/ 목록 페이지
aws s3 cp - "s3://$BUCKET/$PREFIX/index.html" "${HTML[@]}" --only-show-errors <<'EOF'
<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TransMeet 문서</title>
<style>body{font:15px/1.7 system-ui,"Noto Sans KR",sans-serif;max-width:640px;margin:64px auto;padding:0 20px;color:#1d2327}a{color:#34756f}li{margin:6px 0}</style></head>
<body><h1>TransMeet</h1><ul>
<li><a href="guide/">사용 가이드</a></li>
<li><a href="stt-benchmark/">서울 STT 스트리밍 비교 리포트 (2026-10-29)</a></li>
</ul></body></html>
EOF

aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION" \
  --paths "/$PREFIX/*" --query Invalidation.Id --output text >/dev/null
echo "https://archive.hi-yoo.com/$PREFIX/guide/"
echo "https://archive.hi-yoo.com/$PREFIX/stt-benchmark/"
