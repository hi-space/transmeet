# Project: TransMeet

## Overview

- **목적:** 글로벌 미팅용 실시간 번역 앱. 영어 음성을 실시간으로 한글 자막으로 표시하고, 대화 내용을 요약하며, 한글을 영어로 번역하여 음성 출력
- **사용자:** 글로벌 미팅 참석자 (개인용)
- **기술스택:** Next.js (PWA), AWS (SageMaker, Bedrock, Polly, DynamoDB, S3, CloudFront)
- **리포지토리:** https://github.com/hi-space/transmeet

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      CloudFront (CDN)                       │
└─────────────────────────┬───────────────────────────────────┘
                          │
        ┌─────────────────┴─────────────────┐
        │                                   │
        ▼                                   ▼
┌───────────────┐                 ┌───────────────────┐
│   S3 Bucket   │                 │  API Gateway      │
│   (PWA)       │                 │  (WebSocket+REST) │
└───────────────┘                 └─────────┬─────────┘
                                            │
                          ┌─────────────────┼─────────────────┐
                          │                 │                 │
                          ▼                 ▼                 ▼
                   ┌──────────┐      ┌───────────┐     ┌──────────┐
                   │  Lambda  │      │ SageMaker │     │ Bedrock  │
                   │  (API)   │      │ (Whisper) │     │ (Claude) │
                   └──────────┘      └───────────┘     └──────────┘
                          │                                   │
                          │                            ┌──────┴──────┐
                          ▼                            ▼             ▼
                   ┌──────────┐                 ┌──────────┐  ┌──────────┐
                   │ DynamoDB │                 │  Polly   │  │ 번역/요약 │
                   │ (기록)    │                 │  (TTS)   │  │          │
                   └──────────┘                 └──────────┘  └──────────┘
```

## Core Features

### P0 (MVP 필수)

1. **실시간 STT + 번역**
   - 마이크/시스템 오디오 캡처
   - WebSocket으로 오디오 스트리밍
   - SageMaker Whisper로 STT
   - Bedrock Claude로 영→한 번역
   - 실시간 자막 표시

2. **대화 요약/정리**
   - 자동 요약 (N분마다 또는 발언 N개마다)
   - 수동 요약 (버튼 클릭)
   - Bedrock Claude로 요약 생성
   - 핵심 포인트 추출

3. **한→영 번역 + TTS**
   - 텍스트 입력 또는 음성 입력
   - Bedrock Claude로 한→영 번역
   - Amazon Polly로 영어 음성 출력

4. **회의 기록 저장**
   - 전체 대화 기록 저장
   - 요약본 저장
   - 기록 조회/검색

5. **모델 선택**
   - Whisper 모델 선택 (tiny/small/medium/large-v3/large-v3-turbo)
   - 기본값: large-v3-turbo

### P1 (중요)

6. **PWA 기능**
   - 오프라인 기본 UI
   - 홈 화면 설치
   - 푸시 알림 (선택)

### P2 (Nice-to-have)

7. **발언 제안**
   - 대화 컨텍스트 기반 제안
   - 클릭 시 번역 + TTS

## Technical Decisions

- **Frontend:** Next.js 14+ (App Router, PWA)
- **Real-time:** API Gateway WebSocket
- **STT:** SageMaker Whisper (large-v3-turbo, endpoint: whisper-large-v3-turbo-004709)
- **Translation/Summary:** Bedrock Claude (claude-3-sonnet 또는 haiku)
- **TTS:** Amazon Polly (Neural voices)
- **Database:** DynamoDB (on-demand)
- **Storage:** S3 (오디오 백업 선택적)
- **CDN:** CloudFront
- **Region:** us-east-1

## Audio Capture Strategy

### 마이크 입력
- `navigator.mediaDevices.getUserMedia()` 사용
- Web Audio API로 처리

### 시스템 오디오 (상대방 음성)
- `getDisplayMedia()` + audio: true (화면 공유 시)
- 또는 브라우저 확장 프로그램 (제한적)
- 초기 MVP: 마이크 입력 우선, 시스템 오디오는 Phase 2

## Constraints

- us-east-1 리전 고정 (Whisper endpoint 위치)
- 개인용 (멀티테넌트 불필요)
- 인증 없음 (단순화)
- 실시간 처리 지연 < 2초 목표

## Success Criteria

- [ ] 영어 음성 입력 시 2초 내 한글 자막 표시
- [ ] 5분 대화 내용 자동 요약
- [ ] 한글 입력 시 영어 음성 출력
- [ ] 회의 기록 저장 및 조회
- [ ] PWA로 모바일 접속 가능

## Out of Scope (MVP)

- 다국어 지원 (영↔한만)
- 실시간 협업 (여러 명 동시 편집)
- 시스템 오디오 캡처 (브라우저 제한)
- 오프라인 번역

---

*Created: 2025-01-20*
*Last Updated: 2025-01-20*
