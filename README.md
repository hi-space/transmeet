# TransMeet

🎙️ 글로벌 미팅용 실시간 번역 앱

## Features

- ✅ **실시간 STT** — 영어 음성 → 텍스트 (Whisper large-v3-turbo)
- ✅ **실시간 번역** — 영어 → 한글 자막 (Bedrock Claude)
- ✅ **대화 요약** — 자동/수동 요약 생성
- ✅ **한→영 TTS** — 한글 입력 → 영어 음성 출력 (Polly)
- ✅ **회의 기록** — 저장 및 조회
- 🚧 **발언 제안** — Coming soon

## Tech Stack

- **Frontend:** Next.js (PWA)
- **STT:** AWS SageMaker (Whisper)
- **Translation/Summary:** AWS Bedrock (Claude)
- **TTS:** AWS Polly
- **Database:** AWS DynamoDB
- **Hosting:** AWS S3 + CloudFront

## Getting Started

```bash
# Install dependencies
npm install

# Run development server
npm run dev

# Build for production
npm run build
```

## Architecture

```
[Audio Input] → WebSocket → SageMaker Whisper → Bedrock Claude → [한글 자막]
[한글 Input] → Bedrock Claude → Polly TTS → [영어 Audio]
```

## Environment Variables

```bash
# AWS Configuration
AWS_REGION=us-east-1
WHISPER_ENDPOINT=whisper-large-v3-turbo-004709

# Bedrock
BEDROCK_MODEL_ID=anthropic.claude-3-sonnet-20240229-v1:0
```

## License

MIT

---

Built with ❤️ for global communication
