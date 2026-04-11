# TransMeet

글로벌 미팅용 실시간 번역 앱

## Features

- **실시간 STT** — 영어 음성 → 텍스트 (Whisper large-v3-turbo / Amazon Transcribe 선택 가능)
- **실시간 번역** — 영어 ↔ 한글 양방향 번역 (Bedrock Claude Haiku / Sonnet)
- **화자 구분** — Speaker Diarization으로 화자별 메시지 분리
- **대화 요약** — 자동/수동 요약 생성 (Bedrock Claude Sonnet)
- **한→영 TTS** — 한글 입력 → 영어 음성 출력 (Polly)
- **회의 기록** — 생성, 조회, 삭제 + 자동 제목 생성
- **설정 패널** — 오디오 소스, STT 프로바이더, 번역 타이밍, Polly 엔진 등 커스터마이징
- **인증** — AWS Cognito 기반 로그인/회원가입 (선택)
- **다크 모드** — 시스템 설정 감지 + 수동 전환
- **PWA** — 모바일 설치 가능, 오프라인 UI 지원

## Tech Stack

- **Frontend:** Next.js 14 (App Router, Static Export, PWA)
- **STT:** AWS SageMaker (Whisper large-v3-turbo) / Amazon Transcribe
- **Translation/Summary:** AWS Bedrock (Claude Haiku — 번역, Claude Sonnet — 요약)
- **TTS:** AWS Polly (Generative / Neural / Standard)
- **WebSocket:** ECS Fargate + ALB + CloudFront
- **Database:** AWS DynamoDB
- **Auth:** AWS Cognito
- **Hosting:** AWS S3 + CloudFront
- **IaC:** AWS CDK (TypeScript)

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

# Lint & format
npm run lint
npm run format
```

## Project Structure

```
src/
  app/
    layout.tsx              # Root layout (PWA metadata, theme)
    page.tsx                # Main page (상태 관리, WebSocket, 오디오 처리)
    globals.css             # Global styles + Tailwind
  components/
    Header.tsx              # 상단바 (녹음 상태, 테마 토글)
    VoiceArea.tsx           # 음성 입력 메시지 표시 (화자별 카드)
    NotesArea.tsx           # 메모/내 발화 영역
    ChatArea.tsx            # 채팅 뷰
    ControlPanel.tsx        # 녹음/TTS 입력 컨트롤
    MeetingSidebar.tsx      # 회의 목록 & 생성
    SummaryPanel.tsx        # 요약 표시 (Markdown 렌더링 + 복사)
    SettingsPanel.tsx       # 앱 설정 패널
    AuthScreen.tsx          # Cognito 로그인/회원가입
    MobileTabBar.tsx        # 모바일 탭 네비게이션
    SubtitleArea.tsx        # 자막 표시 (레거시)
    ThemeProvider.tsx       # 다크 모드 프로바이더
  hooks/
    useWebSocket.ts         # WebSocket 연결 관리 (재연결, keepalive)
    useAudioCapture.ts      # 오디오 캡처 (마이크/시스템/둘 다)
    useSettings.ts          # localStorage 기반 설정 관리
    useInterval.ts          # 유틸리티 훅
  lib/
    api.ts                  # REST API 클라이언트
    websocket.ts            # WebSocket 메시지 타입 정의
    cognito.ts              # Cognito 인증 유틸리티
  context/
    AuthContext.tsx          # 인증 상태 Context
  types/
    meeting.ts              # Message, Meeting, SpeakerRole 타입
public/
  manifest.json             # PWA manifest
  icons/                    # App icons
```

## Architecture

```
[Audio Input] → WebSocket (ECS Fargate) → SageMaker Whisper / Transcribe → Bedrock Claude → [한글 자막]
[한글 Input]  → Bedrock Claude → Polly TTS → [영어 Audio]
```

## Infrastructure (AWS CDK)

인프라는 `infra/` 폴더에 AWS CDK (TypeScript)로 구성되어 있습니다.

### AWS Resources

| 리소스 | 이름 | 용도 |
|--------|------|------|
| ECS Fargate | `transmeet-ws-cluster` | WebSocket 백엔드 (실시간 오디오 스트리밍) |
| ECR | `transmeet-ws-backend` | WebSocket 백엔드 컨테이너 이미지 |
| ALB | - | ECS 로드밸런싱 |
| API Gateway REST | `transmeet-api` | 회의 CRUD, 요약, TTS |
| Lambda | `transmeet-meetings` | 회의 생성/조회/삭제/제목 생성 |
| Lambda | `transmeet-summarize` | Bedrock Claude 요약 생성 |
| Lambda | `transmeet-tts` | Bedrock 번역 + Polly TTS |
| DynamoDB | `transmeet-meetings` | 회의 기록 저장 |
| S3 | `transmeet-frontend-{account}` | 프론트엔드 정적 호스팅 |
| CloudFront | - | CDN 배포 + WebSocket `/ws` 라우팅 |
| Cognito | `transmeet-users` | 사용자 인증 (선택) |

### REST API Endpoints

```
POST   /meetings                 # 회의 생성
GET    /meetings                 # 회의 목록
GET    /meetings/{id}            # 회의 상세
DELETE /meetings/{id}            # 회의 삭제
POST   /meetings/{id}/summarize  # 요약 생성
POST   /meetings/{id}/title      # 자동 제목 생성
POST   /tts                      # 한→영 번역 + TTS
```

### WebSocket

CloudFront `/ws` 경로를 통해 ECS Fargate 백엔드에 WebSocket 연결합니다.

```
Client → wss://{CloudFront}/ws → ALB → ECS Fargate (포트 8000)
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
    meetings/           # CRUD + 제목 생성
    summarize/          # 요약 생성 (Claude Sonnet)
    tts/                # 한→영 번역 + Polly TTS
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
BEDROCK_MODEL_ID=global.anthropic.claude-haiku-4-5-20251001-v1:0

# API Gateway REST
NEXT_PUBLIC_API_ENDPOINT=https://your-api-gateway-endpoint

# WebSocket (CloudFront /ws)
NEXT_PUBLIC_WS_ENDPOINT=wss://your-cloudfront-domain/ws

# Cognito (CDK 배포 후 설정)
NEXT_PUBLIC_COGNITO_USER_POOL_ID=us-east-1_xxxxxxxxx
NEXT_PUBLIC_COGNITO_CLIENT_ID=xxxxxxxxxxxxxxxxxxxxxxxxxx
NEXT_PUBLIC_COGNITO_REGION=us-east-1
```

## License

MIT
