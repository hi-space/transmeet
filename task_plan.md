# Task Plan: TransMeet

## Overview

- **Spec:** [spec.md](./spec.md)
- **Repository:** https://github.com/hi-space/transmeet
- **Issues:** https://github.com/hi-space/transmeet/issues
- **Started:** 2025-01-20

## Progress

| Status | Count |
|--------|-------|
| ✅ Done | 0 |
| 🔄 In Progress | 0 |
| ⏳ Pending | 10 |

## Tasks

| # | Issue | Title | Status | Branch | PR |
|---|-------|-------|--------|--------|-----|
| 1 | #1 | Project setup (Next.js + PWA) | ⏳ | - | - |
| 2 | #2 | AWS infrastructure (CDK/SAM) | ⏳ | - | - |
| 3 | #3 | Audio capture (마이크) | ⏳ | - | - |
| 4 | #4 | WebSocket 연결 | ⏳ | - | - |
| 5 | #5 | STT 연동 (Whisper) | ⏳ | - | - |
| 6 | #6 | 번역 연동 (Bedrock Claude) | ⏳ | - | - |
| 7 | #7 | 실시간 자막 UI | ⏳ | - | - |
| 8 | #8 | 대화 요약 기능 | ⏳ | - | - |
| 9 | #9 | 한→영 TTS (Polly) | ⏳ | - | - |
| 10 | #10 | 회의 기록 저장/조회 | ⏳ | - | - |

## Dependencies

```
#1 (setup)
  ├── #2 (infra)
  │     └── #4 (websocket)
  │           └── #5 (stt)
  │                 └── #6 (translate)
  │                       └── #7 (ui)
  │                             └── #8 (summary)
  ├── #3 (audio)
  │     └── #4 (websocket)
  └── #9 (tts) - 독립적
  └── #10 (records) - #2 이후

#1 → #2 → #3 → #4 → #5 → #6 → #7 → #8
                              └── #9
                              └── #10
```

## Phase 계획

### Phase 1: 기반 (Task 1-4)
- 프로젝트 셋업
- AWS 인프라
- 오디오 캡처
- WebSocket 연결

### Phase 2: 핵심 기능 (Task 5-7)
- STT 연동
- 번역 연동
- 실시간 UI

### Phase 3: 확장 (Task 8-10)
- 요약 기능
- TTS
- 기록 저장

---

*Last Updated: 2025-01-20*
