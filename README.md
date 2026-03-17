# TransMeet

글로벌 미팅용 실시간 번역 앱

## Features

- **실시간 STT** - 영어 음성 -> 텍스트 (Whisper large-v3-turbo)
- **실시간 번역** - 영어 -> 한글 자막 (Bedrock Claude)
- **대화 요약** - 자동/수동 요약 생성
- **한->영 TTS** - 한글 입력 -> 영어 음성 출력 (Polly)
- **회의 기록** - 저장 및 조회
- **PWA** - 모바일 설치 가능

## Tech Stack

- **Frontend:** Next.js 14 (App Router, PWA)
- **STT:** AWS SageMaker (Whisper large-v3-turbo)
- **Translation/Summary:** AWS Bedrock (Claude)
- **TTS:** AWS Polly
- **Database:** AWS DynamoDB
- **Hosting:** AWS S3 + CloudFront

## Getting Started

```bash
# Install dependencies
npm install

# Copy environment variables
cp .env.example .env.local

# Run development server
npm run dev

# Build for production
npm run build
```

## Project Structure

```
src/
  app/
    layout.tsx        # Root layout (PWA metadata, theme)
    page.tsx          # Main page
    globals.css       # Global styles + Tailwind
  components/
    Header.tsx        # App header with logo + theme toggle
    SubtitleArea.tsx  # Real-time subtitle display (EN + KO)
    ControlPanel.tsx  # Recording/Summarize/TTS controls
    ThemeProvider.tsx # Dark mode provider
public/
  manifest.json       # PWA manifest
  icons/              # App icons
```

## Architecture

```
[Audio Input] -> WebSocket -> SageMaker Whisper -> Bedrock Claude -> [한글 자막]
[한글 Input] -> Bedrock Claude -> Polly TTS -> [영어 Audio]
```

## Infrastructure (AWS CDK)

인프라는 `infra/` 폴더에 AWS CDK (TypeScript)로 구성되어 있습니다.

### AWS Resources

| 리소스 | 이름 | 용도 |
|--------|------|------|
| API Gateway WebSocket | `transmeet-websocket` | 실시간 오디오 스트리밍 |
| API Gateway REST | `transmeet-api` | 회의 CRUD, 요약, TTS |
| Lambda | `transmeet-ws-connect` | WebSocket 연결 처리 |
| Lambda | `transmeet-ws-disconnect` | WebSocket 연결 해제 |
| Lambda | `transmeet-ws-audio` | 오디오 → Whisper STT → Bedrock 번역 |
| Lambda | `transmeet-meetings` | 회의 생성/조회/삭제 |
| Lambda | `transmeet-summarize` | Bedrock Claude 요약 생성 |
| Lambda | `transmeet-tts` | Bedrock 번역 + Polly TTS |
| DynamoDB | `transmeet-meetings` | 회의 기록 저장 |
| DynamoDB | `transmeet-connections` | WebSocket 연결 관리 (TTL 24h) |
| S3 | `transmeet-frontend-{account}` | 프론트엔드 정적 호스팅 |
| CloudFront | - | CDN 배포 |

### REST API Endpoints

```
POST   /meetings              # 회의 생성
GET    /meetings              # 회의 목록
GET    /meetings/{id}         # 회의 상세
DELETE /meetings/{id}         # 회의 삭제
POST   /meetings/{id}/summarize  # 요약 생성
POST   /tts                   # 한→영 번역 + TTS
```

### WebSocket Routes

```
$connect    # 연결 (queryParam: meetingId)
$disconnect # 연결 해제
sendAudio   # 오디오 청크 전송
```

### Deploying Infrastructure

```bash
# Prerequisites
npm install -g aws-cdk
aws configure  # AWS credentials 설정

# CDK bootstrap (최초 1회)
cd infra && npx cdk bootstrap

# 인프라만 배포
./deploy.sh infra

# 프론트엔드만 배포
./deploy.sh frontend

# 전체 배포
./deploy.sh all

# 변경사항 미리 보기
./deploy.sh diff

# 인프라 삭제
./deploy.sh destroy
```

### Infrastructure Directory

```
infra/
  bin/
    transmeet.ts        # CDK app entry point
  lib/
    transmeet-stack.ts  # Main CDK stack
  lambda/
    ws-connect/         # WebSocket $connect
    ws-disconnect/      # WebSocket $disconnect
    ws-audio/           # Audio -> STT -> Translation
    meetings/           # CRUD handler
    summarize/          # Summary generation
    tts/                # Korean -> English TTS
  package.json
  tsconfig.json
  cdk.json
```

## Environment Variables

```bash
# AWS Configuration
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your_access_key_id
AWS_SECRET_ACCESS_KEY=your_secret_access_key

# SageMaker Whisper
WHISPER_ENDPOINT=whisper-large-v3-turbo-004709

# Bedrock
BEDROCK_MODEL_ID=anthropic.claude-3-sonnet-20240229-v1:0

# API Gateway WebSocket
NEXT_PUBLIC_WS_ENDPOINT=wss://your-api-gateway-endpoint

# API Gateway REST
NEXT_PUBLIC_API_ENDPOINT=https://your-api-gateway-endpoint
```

## License

MIT
