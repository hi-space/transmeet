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
