# 번역 모델 검증

2026-10-28에 운영 계정의 `us-east-1`에서 조회하고 호출한 결과입니다. 모델 추가·교체 시 다시 확인합니다.

| 모델             | 글로벌 추론 프로필                  | 상태   | 리전        | 신뢰도   | 확인일     | 재확인 기한 |
| ---------------- | ----------------------------------- | ------ | ----------- | -------- | ---------- | ----------- |
| Claude Haiku 5.5 | `global.anthropic.claude-haiku-5-5` | ACTIVE | `us-east-1` | verified | 2026-10-28 | 2026-11-11  |
| GPT-6 Luna       | `global.openai.gpt-6-luna`          | ACTIVE | `us-east-1` | verified | 2026-10-28 | 2026-11-11  |

- `list-foundation-models`, `list-inference-profiles`로 프로필과 스트리밍 지원을 조회했습니다.
- 두 모델의 `Converse` 일반 응답과 백엔드의 실제 `bedrock_client()`를 이용한 `ConverseStream` 응답을 확인했습니다.
- GPT-6 Luna는 영어→한국어, 한국어→영어 스트리밍을 확인했습니다.
- 기존 WebSocket 태스크 역할과 Lambda 역할의 모델 호출·스트리밍 호출 권한은 IAM 정책 시뮬레이션에서 `allowed`로 확인했습니다.
- 번역 요청은 기존과 같이 `maxTokens`만 설정합니다. `temperature`를 보낸 사전 호출은 두 모델 모두 거절했습니다.
- 단문 확인은 호환성 검사이며, 일반적인 지연 시간이나 번역 품질을 보장하는 벤치마크가 아닙니다.

UI의 모델 목록과 저장값 검증은 `src/lib/translationModels.ts`에서 관리합니다. 기본값은 Haiku 5.5이며, 제거된 모델 또는 잘못된 저장값은 기본값으로 복원합니다.

GPT-6 Luna 선택의 문서 근거는 OpenAI의 저지연·고처리량 작업용 모델 설명입니다 (**documented**).

공식 모델 문서: `https://developers.openai.com/api/docs/models/gpt-6-luna`
