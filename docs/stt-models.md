# STT 엔진 선택과 한국어 비교

설정 → 음성·번역 → 고급 설정 → **음성 인식 엔진**에서 선택한다.

| 화면 이름 | 내부 ID | 연결 방식 | 언어 처리 |
|---|---|---|---|
| Transcribe | `transcribe` | Amazon Transcribe의 지속 스트리밍 세션 | 선택 언어 또는 자동 감지 |
| Whisper | `whisper` | 기존 SageMaker Whisper large-v3-turbo | 기존 바이너리 WAV 요청, 모델의 자동 감지 |
| Qwen3-ASR | `qwen3-asr` | SageMaker Inference Component / Qwen3-ASR-1.7B | JumpStart 기본 채팅 인터페이스의 자동 감지 |
| Nemotron 3.5 | `nemotron` | SageMaker Inference Component / Nemotron 3.5 ASR 0.6B | `ko-KR`, `en-US`, `auto`를 모델에 전달 |

Qwen과 Nemotron은 이번 연결에서 완성된 음성 구간을 인식한다. 모델 자체가 제공하는 캐시 기반 스트리밍 기능은 사용하지 않으며, 발화 중 부분 자막은 Transcribe 경로에서 제공한다. Qwen의 기본 JumpStart 채팅 요청에는 강제 언어 옵션을 넣지 않는다. 앱에서 한국어를 선택하면 번역 방향은 고정되지만, Qwen의 디코딩 언어를 강제하는 것은 아니다.

브라우저 전송 간격은 Transcribe 700ms, SageMaker 엔진 2초다. 서버는 수신 후 600ms 타이머로 버퍼를 처리하므로 일반적으로 SageMaker 모델에 2초 구간이 들어간다. 이 타이머는 실제 오디오의 무음 길이를 측정하는 VAD가 아니다. 문맥 길이가 정확도에 미치는 영향을 확인하려면 아래 비교 도구의 전체 문장 모드와 2초 구간 모드를 함께 실행한다.

엔진을 변경하면 브라우저에 남은 음성을 먼저 보내고, 이전 세션의 인식·번역을 마무리한 뒤 새 설정을 적용한다. 미설정 엔드포인트는 오류를 표시하며 다른 엔진으로 자동 대체하지 않는다.

## 사용하지 않을 때 GPU 0대

Qwen과 Nemotron은 엔드포인트·모델·Inference Component를 보존하면서 실행 복제본을 0개로 줄인다. 일반 모델 기반 엔드포인트의 인스턴스 수를 단순히 0으로 바꾸는 방식이 아니다.

- 저장된 엔진을 불러오거나 페이지를 여는 것만으로 GPU를 기동하지 않는다.
- 설정에서 새 엔진을 선택하거나 녹음 조작의 **엔진 준비**를 누르면 복제본 1개를 요청한다.
- **준비 중**에는 녹음을 시작하지 않는다. **사용 가능**이 되면 녹음을 시작할 수 있다.
- 마이크 권한이 거부되면 서버에 녹음 시작을 보내지 않는다.
- 활성 녹음과 진행 중인 음성 처리가 모두 끝나고 `STT_IDLE_SECONDS`(기본 300초) 동안 사용하지 않으면 복제본을 0으로 내린다.
- `IDLE_RELEASE` 정책이 빈 GPU 인스턴스를 반납한다. 복제본만 0이고 GPU가 아직 남았으면 **대기 상태로 전환 중**, 실제 인스턴스도 0이면 **대기 중 · GPU 0대**로 표시한다.
- 일시적인 상태 조회 실패는 진행 중인 녹음을 중단하지 않는다. 확인된 용량 상실이나 서버의 녹음 거절은 별도로 처리한다.

기동에는 수분 이상 걸릴 수 있으며 리전의 GPU 재고에 영향을 받는다. 최초 생성 때는 엔드포인트와 모델 복제본을 각각 1개로 시작해야 한다. 이 세션의 실제 API는 최초 `CreateInferenceComponent(CopyCount=0)`을 거절했으므로, 생성 완료 뒤 0으로 변경한다.

네이티브 자동 확장은 실제 호출량과 `TransMeet/STT`의 `ActiveUse` 지표 중 큰 값을 사용한다. 백엔드는 활성 녹음·진행 중인 작업·기동 대기 및 유휴 유예 기간에 `ActiveUse=1`을 보낸다. 따라서 대화 중 잠깐 조용해져도 모델을 내리지 않는다. 누락된 지표는 0으로 처리해 백엔드가 중단돼도 영구적으로 GPU가 남지 않게 한다. 별도의 `NoCapacityInvocationFailures > 0` 알람은 용량 없이 호출됐을 때 확장을 요청한다.

## 모델 지원 확인

조회일 `2026-10-28`, 재확인 권장일 `2026-11-11`. `verified`는 이 세션에서 AWS API로 확인한 항목, `documented`는 제작사 문서에서 확인한 항목이다. 카탈로그에 있는 것과 실제 엔드포인트 호출 성공은 별개의 확인이다.

| 항목 | 리전 | 상태 | 근거 |
|---|---|---|---|
| Qwen 모델 ID `huggingface-asr-qwen3-asr-1-7b`, 버전 `1.0.2` | us-east-1 | verified | `SageMakerPublicHub`의 `DescribeHubContent` |
| Qwen 기본 인스턴스 `ml.g6.xlarge` | us-east-1 | verified | 같은 모델의 배포 메타데이터 |
| Nemotron 모델 ID `huggingface-asr-nvidia-nemotron-3-5-asr-streaming-0-6b`, 버전 `1.0.2` | us-east-1 | verified | `SageMakerPublicHub`의 `DescribeHubContent` |
| Nemotron 기본 인스턴스 `ml.g5.2xlarge` | us-east-1 | verified | 같은 모델의 배포 메타데이터 |
| Qwen의 한국어 지원 | 모델 공통 | documented | Qwen 제작사 모델 카드의 30개 언어 목록 |
| Nemotron의 한국어 지원 | 모델 공통 | documented | NVIDIA 제작사 모델 카드의 transcription-ready `ko-KR` |

Qwen의 라이선스는 Apache-2.0, Nemotron은 OpenMDW License Agreement로 카탈로그에 표시된다.

공식 근거:

- [AWS의 Qwen3-ASR JumpStart 출시 공지](https://aws.amazon.com/about-aws/whats-new/2026/05/speech-models-on-sagemaker-jumpstart/)
- [Qwen3-ASR-1.7B 모델 카드](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)
- [Nemotron 3.5 ASR 모델 카드](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
- [FLEURS 데이터셋](https://huggingface.co/datasets/google/fleurs)
- [SageMaker: Scale an endpoint to zero instances](https://docs.aws.amazon.com/sagemaker/latest/dg/endpoint-auto-scaling-zero-instances.html)
- [SageMaker: Heterogeneous instance pools](https://docs.aws.amazon.com/sagemaker/latest/dg/realtime-endpoints-heterogeneous.html)

## 엔드포인트 배포와 앱 설정

배포용 환경에는 SageMaker Python SDK v2가 필요하다. 이 작업에서는 `2.257.1`을 사용했다. 백엔드 런타임에는 SDK를 추가로 설치하지 않아도 된다.

먼저 계획을 조회한다. `--apply`가 없는 명령은 리소스를 만들지 않는다.

```bash
python backend/scripts/deploy_stt_scaling.py qwen3-asr --role "$SAGEMAKER_ROLE_ARN"
python backend/scripts/deploy_stt_scaling.py nemotron --role "$SAGEMAKER_ROLE_ARN"
```

실제 배포는 SageMaker 실행 역할을 지정한다. 리소스는 삭제하지 않는다. 기존 엔드포인트의 구성 변경에는 명시적인 `--migrate`가 필요하다.

```bash
python backend/scripts/deploy_stt_scaling.py qwen3-asr \
  --apply --wait --role "$SAGEMAKER_ROLE_ARN"
python backend/scripts/deploy_stt_scaling.py nemotron \
  --apply --wait --role "$SAGEMAKER_ROLE_ARN"
```

기본 엔드포인트 이름은 `transmeet-qwen3-asr`, `transmeet-nemotron-asr`다. 컴포넌트 이름은 `transmeet-qwen3-asr-ic-v2`, `transmeet-nemotron-asr-ic-v2`다. Qwen은 `ml.g6.xlarge`, `ml.g6.2xlarge` 우선순위 풀을 사용한다. 스크립트는 리전의 모델 메타데이터로 지원 여부를 검사한다.

```bash
export QWEN_ASR_ENDPOINT=transmeet-qwen3-asr
export NEMOTRON_ASR_ENDPOINT=transmeet-nemotron-asr
export QWEN_ASR_INFERENCE_COMPONENT=transmeet-qwen3-asr-ic-v2
export NEMOTRON_ASR_INFERENCE_COMPONENT=transmeet-nemotron-asr-ic-v2
export STT_IDLE_SECONDS=300
```

로컬 백엔드는 환경변수 또는 `.env`에서 읽는다. CDK는 같은 환경변수 또는 `qwenAsrEndpoint`, `nemotronAsrEndpoint`, `qwenAsrComponent`, `nemotronAsrComponent` 컨텍스트를 읽어 ECS에 반영한다. CDK 기본값은 위의 운영 이름이다. 실행 역할에는 해당 엔드포인트 조회·호출, 해당 컴포넌트 조회·복제본 수 변경, `TransMeet/STT` 네임스페이스의 지표 전송만 추가한다.

```bash
cd infra
npx cdk diff \
  -c qwenAsrEndpoint=transmeet-qwen3-asr \
  -c nemotronAsrEndpoint=transmeet-nemotron-asr
```

운영 반영에는 CDK 설정 변경, 백엔드 이미지 빌드·푸시와 ECS 재배포, 프런트엔드 배포가 필요하다. 기존 절차는 루트 README의 배포 섹션을 따른다. 엔드포인트를 설정한 환경변수나 CDK 컨텍스트는 이후 배포에도 유지해야 한다.

AWS Pricing API의 `USE1-Host:ml.g6.xlarge`, `USE1-Host:ml.g5.2xlarge` 조회 기준 인스턴스 비용은 각각 시간당 `$1.1267`, `$1.5150`이다(2026-10-28, 온디맨드). 두 개가 실행 중이면 합계 시간당 `$2.6417`이며 스토리지·지표·알람·전송·기존 Whisper·Transcribe 비용은 제외한다. Qwen 대체 풀인 `ml.g6.2xlarge`는 시간당 `$1.2220`이다. 실제 GPU 인스턴스가 0대인 동안 해당 GPU 호스팅 비용은 발생하지 않는다.

## 같은 음성으로 비교하기

16 kHz, mono, 16-bit PCM WAV와 사람이 확인한 정답 문장을 준비한다. 경로는 매니페스트 파일 기준이다.

```json
{
  "source": "직접 녹음한 한국어 회의 발화",
  "samples": [
    {
      "id": "ko-01",
      "audio": "ko-01.wav",
      "language": "ko",
      "reference": "다음 주 화요일에 배포하겠습니다."
    }
  ]
}
```

```bash
# 전체 녹음 구간을 동일하게 전달
python backend/scripts/benchmark_stt.py samples/manifest.json \
  --output results/full.json

# 앱의 SageMaker 입력 길이에 가까운 2초 구간
python backend/scripts/benchmark_stt.py samples/manifest.json \
  --chunk-seconds 2 --output results/chunked.json
```

`--providers whisper transcribe qwen3-asr nemotron`, `--repeat 3`, `--timeout 90`을 지정할 수 있다. 자동 감지 비교는 매니페스트의 `language`를 `auto`로 설정한다.

측정 기준:

- **CER:** NFKC 정규화 후 대소문자·공백·문장부호 차이를 제거한 문자 오류율. 숫자와 한글 수사, 영문 약어와 발음 표기는 임의로 같은 것으로 바꾸지 않는다.
- **집계:** 샘플별 CER의 단순 평균이 아닌, 전체 오류 문자 수 ÷ 전체 정답 문자 수.
- **시간:** 이미 녹음된 구간의 호출 시작부터 최종 결과까지 걸린 시간. Transcribe에도 실시간 속도로 대기하지 않고 녹음을 전달한다. 실제 마이크 입력에서 자막이 나오는 지연 시간과는 다르다.
- **2초 모드:** SageMaker만 입력을 나누고 결과를 합친다. Transcribe는 앱과 같이 하나의 지속 세션을 사용한다. 앱의 무음·환각 필터와 번역 시간은 포함하지 않는다.
- **실패:** 각 샘플의 오류를 기록하며, 실패가 하나라도 있는 모델은 종합 CER을 표시하지 않는다.

인식된 원문, 정답, 오류 문자 수, 지연 시간, 실패 메시지는 JSON 결과에 모두 남는다. 짧은 공개 데이터 비교는 회의 잡음·사투리·전문 용어 성능을 보장하지 않는다.

## 입력 길이의 영향: 2026-10-28 측정

FLEURS 한국어 테스트 세트의 연속된 10개 녹음(행 0–9, 111.12초, 정규화한 정답 447자)을 사용했다. 각 조건을 한 번씩 측정했으며, 임의로 선택한 회의 녹음이나 전체 FLEURS 성능을 대표하지 않는다.

| 동일한 Whisper large-v3-turbo에 전달한 입력 | CER |
|---|---:|
| 전체 녹음 구간 | 3.58% |
| 6초씩 나눈 구간 | 15.44% |
| 2초씩 나눈 구간 | 43.40% |
| 2초 구간 + 모델에 한국어 명시 | 40.04% |

고정된 시간 경계는 단어 중간을 자르고 문맥을 줄인다. 한국어를 명시하는 것만으로 이 차이가 해소되지 않았다. 발화 경계에 맞춘 버퍼링이나 모델의 지속 스트리밍을 검토할 근거이며, 이 변경에서는 엔진 비교 조건을 유지하기 위해 기존 2초 전송 간격을 유지했다.

Nemotron은 숫자를 `15` 대신 `십오`처럼 출력하기도 한다. 이 보고서의 CER은 그 표기 차이를 포함한다. 따라서 점수와 함께 JSON의 정답·인식 원문을 확인해야 한다.
