# Whisper 실시간 자막 검증

기존 JumpStart Whisper large-v3-turbo에 SageMaker 양방향 스트리밍 서버를
추가했다. 현재 발화의 누적 음성을 주기적으로 다시 인식하며, Whisper
모델 자체가 캐시를 이어 쓰는 네이티브 스트리밍 방식은 아니다.

## 실행 구성

- 리전: `us-east-1`
- 모델: `huggingface-asr-whisper-large-v3-turbo`
- JumpStart 가중치: `inference-prepack/v1.0.0/`
- PyTorch `2.1.0+cu118`, Transformers `4.39.3`
- Silero VAD `6.2.3`, CPU ONNX Runtime `1.23.2`, 음성 확률 기준 `0.5`
- 브라우저 전송: 160ms, 모델 갱신 간격: 1000ms 또는 2000ms
- 발화 경계: 상대적인 무음 800ms 또는 연속 15초
- 기존 배치 API: 최대 30초 PCM WAV, 16kHz mono 정규화
- 이미지 식별값: [image.json](image.json)
- 검증용 구성: [staging-plan.json](staging-plan.json)
- 운영용 구성: [production-plan.json](production-plan.json)

`sttStreaming: true`로 시작한 녹음만 새 경로를 사용한다. 구버전 화면이
이 필드 없이 보내는 6초 WAV는 계속 배치로 처리한다. 부분 자막은 수정될
수 있으며, 확정된 발화만 번역하고 회의에 저장한다.

## 로컬 검증

- 전체 백엔드: 626개 통과. 기존 Pydantic 설정 방식의 폐기 예정 경고 3개.
- 전체 프런트엔드 단위 테스트: 121개 통과.
- 프로덕션 프런트엔드 빌드 및 인프라 TypeScript 검사 통과.
- 고정된 컨테이너에서 HTTP 상태 확인, WAV 배치 요청, WebSocket
  ready/partial/final/ended 흐름 통과. `pip check` 충돌 없음.
- 브라우저 시나리오 6개 통과: 최초 기능 확인, 짧은 오디오 프레임,
  확정 결과만 반영, 배치 호환, 언어 전환, 재연결 및 짧은 마지막 음성.
- 독립 코드 검토에서 발견한 구버전 6초 입력 문제를 재현하고 녹음별
  명시적 스트리밍 선택으로 수정했다.

## 실제 GPU 측정

상태: 실제 GPU에서 40회 스트리밍 측정과 운영 앱 검증 완료.
한국어·영어 각 10개의 기존 FLEURS WAV와 해시를 사용하며, 파일을 한 번에
보내지 않고 160ms 오디오의 수집이 끝난 시점에 전송한다.

| 언어 | 모델 갱신 간격 | 성공 | 최종 인식 오류율 | 첫 부분 자막 중앙값 | 파일 끝 기준 최종 결과 지연 중앙값 |
|---|---|---|---|---|---|
| 한국어 | 1초 | 10/10 | CER 4.25% | 2.59초 | 0.32초 |
| 한국어 | 2초 | 10/10 | CER 4.25% | 2.52초 | 0.35초 |
| 영어 | 1초 | 10/10 | WER 3.79% | 1.51초 | 0.52초 |
| 영어 | 2초 | 10/10 | WER 3.79% | 2.53초 | 0.48초 |

40회 모두 입력이 끝나기 전에 부분 자막이 도착했고, 1초와 2초 간격의
최종 텍스트는 각 파일에서 동일했다. 기본값은 1초로 선택했다. 이 시간은
스트림 준비 이후 오디오 전송 시작부터 측정하며, 파일 앞부분의 무음도
포함한다. 준비 시간(중앙값 약 1.1초)은 별도다. 파일 뒤 무음보다 먼저
결과가 확정되면 파일 끝 기준 지연은 음수가 될 수 있다. 측정 인스턴스는
검증용 `ml.g5.xlarge`다. 운영도 같은 인스턴스를 사용하도록 구성했다.

한국어 기존 전체 문장 배치 CER은 3.58%, 스트리밍 CER은 4.25%다.
18.9초 문장이 15초 경계에서 나뉘며 한 곳에서 인식 차이가 발생했다.
다른 한국어 파일과 영어 파일의 최종 텍스트는 기존 배치 결과와 같았다.
이는 20개 샘플의 결과이며 모든 회의 환경의 정확도를 보장하는 수치는 아니다.


첫 버전은 실제 GPU에서 약 1.5초 후 부분 자막을 보냈지만, 발화 뒤
배경음에서 “감사합니다/Thank you”를 추가로 생성했다. 운영에 반영하지
않았으며 [초기 결과](baseline-room-tone/README.md)를 보존했다.
두 번째 실험의 Whisper 자체 ‘음성 없음’ 확률도 이 체크포인트에서는
유효한 신호를 주지 않았다. [실험 기록](baseline-nospeech-probability/README.md)을
보존했다. 현재 버전은 별도의 Silero VAD가 음성 여부를 판별하며, 생성된
문구를 차단 목록에 넣지 않는다. 실제로 말한 짧은 응답과 작은 목소리가
유지되는지도 별도 음성 8개로 검증했다. GPU에서 8개 모두 문자 오류 없이 인식했고, 한·영 원본 샘플에서
가짜 추가 문장이 사라졌다.

로컬 VAD와 기존 GPU의 배치 인식을 조합한 사전 검증에서는 배경음 구간을
제외한 뒤 영어 10개 결과가 기존 전체 문장 인식과 같았다. 한국어는
15초 경계에서 분리된 18.9초 문장 한 곳에 차이가 있었다. 이 검증은
스트리밍 지연 측정이 아니며 [별도 결과](composed-quality-check.json)에 보관한다.

```bash
NEMOTRON_STREAM_PYTHON=/path/to/isolated-sdk/bin/python \
python backend/scripts/benchmark_whisper_streaming.py \
  --manifest benchmarks/stt-ko-en-2026-10-28/manifest-ko.json \
  --endpoint transmeet-whisper-streaming --region us-east-1 \
  --chunk-ms 1000 \
  --output benchmarks/whisper-streaming-2026-10-28/ko-1000.json
```

## 배포 및 복구

운영 `whisper-large`는 스트리밍·배치 겸용 컨테이너로 전환됐다.
`WHISPER_STREAMING_ENABLED=true`, 모델 갱신 간격은 1000ms이며,
운영 인스턴스는 `ml.g5.xlarge` 한 대다. 배포 식별값은
[deployment.json](deployment.json)에 기록했다.

실제 Cognito 인증과 운영 WebSocket 경로로 한국어 음성 12.48초를 전송했다.
첫 부분 자막은 약 2.75초에 도착했고, 영어 번역을 포함한 최종 결과 한 건이
회의에 한 번 저장됐다. 같은 서버에 구버전 방식의 6초 WAV를 보내는 시험도
부분 자막 없이 최종 결과 한 건을 저장했다. 각각
[스트리밍 앱 검증](application-stream.json),
[구버전 앱 검증](application-legacy.json)에 기록했다.

검증용 엔드포인트와 해당 모델·설정 세 개, 임시 테스트 계정을 제거했다.
이전 정적 파일은 캐시된 화면의 호환성을 위해 유지했다.
정리 결과는 [cleanup.json](cleanup.json)에 있다.

기존 `whisper-large`의 배포 전 설정은
`whisper-large-1770857274414`이며, 이 설정과 기존 모델을 삭제하지 않는다.

복구할 때에는 먼저 백엔드의 `WHISPER_STREAMING_ENABLED`를 끄거나 이전
ECS 작업 정의로 되돌려 새 녹음이 배치 경로를 선택하게 한다. 그다음
`whisper-large`를 이전 엔드포인트 설정으로 갱신한다.

기존 운영 크기 `ml.g5.2xlarge`로 시도한 교체는 AWS의
`InsufficientInstanceCapacity` 오류로 자동 복구됐다. 해당 기록은
`capacity-fallback/`에 남겼으며, 검증에 사용한 `ml.g5.xlarge`로 재시도했다.

선택한 운영 GPU는 `ml.g5.xlarge`($1.408/시간), 이전 운영 GPU는
`ml.g5.2xlarge`($1.515/시간)다. 이는 2026-10-28 AWS Pricing API에서
조회한 버지니아 리전 온디맨드 호스팅 요금이며, ECR·S3·로그·전송 비용은
포함하지 않는다. 운영 전환 후 검증용 엔드포인트를 제거해 운영 Whisper
GPU 한 대를 유지한다.
