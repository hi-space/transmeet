# STT 엔진 선택과 한국어·영어 비교

한국어·영어 각 10개 음성의 전체 문장·6초 분할 실측은 [2026-10-28 비교 보고서](../benchmarks/stt-ko-en-2026-10-28/README.md)에 있다. Qwen과 Nemotron 배치 엔진은 서울(`ap-northeast-2`)에 각각 GPU·모델 복제본 1개를 유지한다.

설정 → 음성·번역 → 고급 설정 → **음성 인식 엔진**에서 선택한다.

상단의 **입력 · …**에서 음성 언어를, **번역 · …**에서 번역 대상을 독립적으로 선택한다. 선택창 위의 고정 라벨 없이 두 메뉴와 감지 상태를 한 줄로 표시하며, 좁은 화면에서는 이 행이 헤더 바로 아래에 놓인다.

- **음성 언어:** 자동 감지(한·영), 한국어, English.
- **번역 대상:** 감지 언어의 반대편, 한국어, English.
- 자동 감지 + 한국어를 선택하면 한국어·영어 입력 모두 한국어 결과를 제공한다. 이미 한국어인 발화는 모델로 재작성하지 않고 원문을 유지한다.
- 감지 언어의 반대편을 선택하면 한국어 발화는 영어로, 영어 발화는 한국어로 번역한다.
- 신규 설정의 기본값은 영어 입력 → 한국어 번역이다. 기존 AUTO 설정은 반대편 언어 번역으로 이전되며, 기존 고정 방향도 보존된다.
- `감지: 한국어` / `감지: English`는 실제 인식 결과다. 선택한 입력 언어와 다른 결과도 그 언어를 덮어쓰지 않는다.

| 화면 이름 | 내부 ID | 연결 방식 | 언어 처리 |
|---|---|---|---|
| Transcribe | `transcribe` | Amazon Transcribe의 지속 스트리밍 세션 | 고정 언어 또는 한·영 다국어 자동 식별 |
| Whisper | `whisper` | 기존 SageMaker Whisper large-v3-turbo | 자동은 바이너리 WAV, 고정 언어는 JSON 요청으로 `korean` / `english` 전달 |
| Qwen3-ASR | `qwen3-asr` | SageMaker Inference Component / Qwen3-ASR-1.7B | 현재 연결은 자동 감지 전용. 번역 대상은 독립적으로 선택 |
| Nemotron 3.5 | `nemotron` | 활성화 시 SageMaker 양방향 스트리밍 / Nemotron 3.5 ASR 0.6B, 비활성화 시 기존 배치 경로 | `ko-KR`, `en-US`, `auto`를 모델에 전달 |

Transcribe의 AUTO는 `identify_multiple_languages=True`, `language_options=["en-US", "ko-KR"]`를 사용한다. 단일 언어 식별인 `identify_language`와 동시에 보내지 않는다. 고정 언어에서는 `language_code`만 지정한다. 화자 구분은 두 방식 모두 유지한다.

Whisper 고정 언어 요청은 기존 테스트 앱과 같은 `audio_input`(WAV의 hex 문자열), `language`, `task="transcribe"` JSON 형식을 사용한다. Whisper가 영어 번역까지 수행하게 하지 않고, 원문 인식 이후 별도의 번역 대상 설정을 적용한다.

Qwen과 Nemotron의 기존 배치 경로는 완성된 음성 구간을 인식한다. Nemotron 스트리밍을 활성화하면 모델의 캐시 기반 연속 인식을 사용하고, Transcribe와 마찬가지로 발화 중 부분 자막을 표시한다. Qwen의 현재 JumpStart 채팅 요청에는 강제 언어 옵션을 넣지 않는다. Qwen 선택 중에는 음성 언어 선택창이 자동 감지로 표시되고 비활성화되며, 다른 엔진으로 돌아오면 이전 입력 언어 선택을 복원한다. Qwen 모델 자체의 언어 지정 능력과 현재 앱의 요청 형식은 구분해야 한다.

앱의 지원 범위는 한국어·영어다. AUTO는 선택한 엔진 안에서 언어를 처리하며 엔진을 자동 교체하지 않는다. 같은 음성 구간 안에서 두 언어가 섞인 경우의 단어별 분리나 인식 정확도까지 보장하는 설정은 아니다.

클라이언트의 새 `translationTarget` 필드는 `auto | ko | en`이다. 이 필드가 없는 이전 WebSocket 요청은 기존 `sourceLang` / `targetLang` 의미를 유지한다. 각 발화의 실제 `detectedLanguage`와 `targetLanguage`를 결과와 저장 데이터에 남기고, 둘 중 하나라도 다르면 화면에서 말풍선을 합치지 않는다.

브라우저 전송 간격은 Transcribe 700ms, 활성화된 Nemotron 스트리밍 160ms, 배치 엔진(Whisper·Qwen·기존 Nemotron) 기본 6초다. 배치 엔진에서는 설정 → 음성·번역 → 고급 설정 → **음성 처리 구간**에서 2·4·6·8·10초를 선택할 수 있으며, 선택은 이 브라우저에 저장된다. 기존 설정에 값이 없거나 잘못된 값이면 6초를 사용한다. 브라우저는 서버 상태의 `streaming: true`를 확인한 뒤에만 Nemotron을 짧은 전송 간격으로 전환하므로, 이전 백엔드나 비활성화된 환경에서는 기존 배치 설정이 유지된다.

Transcribe와 활성화된 Nemotron은 전송 사이에도 스트리밍 세션을 유지하므로 배치 구간 선택을 적용하지 않는다. 배치 경로의 서버는 수신 후 600ms 타이머로 버퍼를 처리하므로 모델에는 보통 선택한 길이의 구간이 들어간다. 이 타이머는 실제 오디오의 무음 길이를 측정하는 VAD가 아니다.

무음 판정은 수집 구간 전체의 평균 대신 기존 2초 단위 구간 중 가장 큰 RMS 에너지를 사용한다. 짧은 발화 뒤에 긴 무음이 붙어도, 기존 2초 입력에서 통과하던 발화가 긴 입력의 평균에 희석되어 누락되지 않도록 한다.

배치 엔진은 첫 결과가 나오기 전에 선택한 구간만큼의 입력 수집 시간과 추론 시간이 필요하다. 녹음 중지·회의 종료·입력 언어·번역 대상·엔진 변경 시에는 간격을 채우지 않고 남은 음성을 즉시 전송한다. 녹음 중 처리 구간을 변경할 때도 기존 음성을 먼저 전송한 뒤 새 간격을 적용한다. 문맥 길이의 영향을 비교하려면 전체 문장, 기본값인 6초, 이전 기본값인 2초 조건을 함께 확인한다.

엔진·입력 언어·번역 대상을 변경하면 브라우저에 남은 음성을 먼저 보내고, 이전 세션의 인식·번역을 이전 설정으로 마무리한 뒤 새 설정을 적용한다. 미설정 엔드포인트는 오류를 표시하며 다른 엔진으로 자동 대체하지 않는다.

언어 설정 변경의 회귀 검사는 `npm run test:speech`, `cd backend && python -m pytest -q`로 실행한다. `tests/speech_languages_browser.py`는 제어된 API/WebSocket 응답과 브라우저의 가상 마이크로 설정 이전, 모델 전환, 녹음 버퍼 전송 순서, 부분 자막·수동 재번역·늦은 응답, 모바일 화면을 검사한다. 이 검사는 실제 AWS 모델의 인식 정확도 측정과 구분한다.

## Nemotron 연속 인식

원본 모델은 `nvidia/nemotron-3.5-asr-streaming-0.6b`, 모델 파일과 Transformers 실행 환경은 기존 JumpStart `1.0.2` 아티팩트다. `inference/nemotron_streaming/`의 별도 서버가 SageMaker 양방향 WebSocket 계약을 구현한다. 같은 발화의 특징·디코더 캐시는 다음 음성 조각에 이어서 사용한다. 서로 다른 녹음은 실행 상태를 공유하지 않으며, GPU 가중치만 공유한다.

- 브라우저의 **160ms 전송 간격**과 모델의 **320/560ms 처리 설정**은 별개다. 첫 자막 지연에는 음성 내용, 네트워크, 추론 시간도 포함된다.
- 20ms 단위 RMS로 발화 시작과 무음을 판정하고, 시작 전 최대 200ms를 보존한다. 기본 확정 기준은 700ms 무음이며, 30초 연속 발화에서도 구간을 확정해 상태 크기를 제한한다. 이 방식은 학습된 화자 분리나 잡음 제거 모델이 아니다.
- 부분 자막은 현재 가설 전체를 갱신한다. 확정된 발화만 번역·저장하며, 녹음 정지와 설정 변경은 남은 짧은 오디오까지 배출한다. 이전 구간의 늦은 결과가 새 부분 자막을 지우지 않도록 스트림·발화 ID를 사용한다.
- SageMaker 연결은 25분마다 기존 구간을 마무리하고 재연결한다. 입력과 결과 대기열에 상한이 있으며, 처리 실패는 녹음 중단 상태로 전달한다.
- 스트리밍 API에는 추론 컴포넌트 선택 필드가 없어, `transmeet-nemotron-streaming-v2` 단일 모델 엔드포인트를 사용한다. 기존 배치 엔드포인트·컴포넌트는 별도로 보존한다.
- 기존 Transcribe와 HTTP/2 SDK가 요구하는 `awscrt` 버전이 달라, HTTP/2 연결은 별도 Python 가상환경의 프로세스로 실행한다. 백엔드 Docker 이미지가 `/opt/nemotron-stream`을 구성한다.

백엔드 활성화 설정:

```dotenv
NEMOTRON_STREAMING_ENABLED=true
NEMOTRON_STREAMING_ENDPOINT=transmeet-nemotron-streaming-v2
NEMOTRON_STREAMING_REGION=us-east-1
NEMOTRON_STREAMING_CHUNK_MS=560
NEMOTRON_STREAM_PYTHON=/opt/nemotron-stream/bin/python
```

로컬 실행에서는 `aws-sdk-sagemaker-runtime-http2[awscrt]==0.12.0`과 `boto3==1.43.106`을 별도 가상환경에 설치하고 그 Python 경로를 지정한다. `NEMOTRON_STREAMING_ENABLED=false`이면 기존 `NEMOTRON_ASR_ENDPOINT`를 사용한다.

컨테이너를 빌드하고 ECR에 올린 뒤, 배포 계획을 먼저 조회한다. `--apply`를 추가해야 실제 리소스를 만들며 기존 스트리밍 엔드포인트의 교체에는 `--update`도 필요하다.

```bash
docker build --platform linux/amd64 -f inference/nemotron_streaming/Dockerfile -t nemotron-streaming .
python backend/scripts/deploy_nemotron_streaming.py \
  --image "$NEMOTRON_IMAGE_DIGEST_URI" --role "$SAGEMAKER_ROLE_ARN"
```

기본 호스트는 `ml.g5.xlarge` 한 대다. 첫 배포에서 `ml.g5.2xlarge`의 계정 할당량이 소진되어, 동일한 A10G GPU를 제공하는 유형으로 선택했다. 모델에 포함된 CUDA 12.4 PyTorch에 맞춰 `InferenceAmiVersion=al2-ami-sagemaker-inference-gpu-3-1`을 지정한다. G5 기본 AMI에서는 실제로 드라이버 버전 불일치가 발생했다. 스트리밍 서버는 최대 4개 연결과 최대 4개 실제 디코더 작업을 허용한다. 연결이 취소돼도 GPU 작업이 종료되기 전에는 작업 슬롯을 재사용하지 않는다.

## GPU 1대씩 상시 유지

배치 추론 구성의 Qwen과 Nemotron은 각각 모델 복제본 1개를 유지한다. GPU를 다시 확보하고 모델을 올리는 데 수분 이상 걸릴 수 있어, 기본 운영 정책을 상시 유지로 변경했다. 별도 Nemotron 스트리밍 엔드포인트도 GPU 한 대를 유지하므로, 배치 구성을 함께 보존하는 동안에는 그 호스팅 비용이 추가된다.

- Application Auto Scaling의 복제본 최소·최대 용량과 SageMaker GPU 호스트의 최소·최대 수량을 모두 1로 설정한다. 상시 유지 배포 설정은 `warm-v1` 이름을 사용한다.
- `STT_IDLE_SECONDS=0`이 기본값이다. 0 이하이면 백엔드는 유휴 상태에서 복제본을 내리지 않고, 구성된 엔진에 `ActiveUse=1`을 보낸다.
- 페이지를 닫거나 녹음을 끝내도 두 모델은 준비 상태를 유지한다.
- 실제 장애나 수동 중지 후에는 **엔진 준비**로 기동을 요청할 수 있다. **준비 중**에는 녹음을 시작하지 않으며, 실제 복제본·GPU 상태가 확인돼야 **사용 가능**으로 표시한다.
- 일시적인 상태 조회 실패는 진행 중인 녹음을 중단하지 않는다. 확인된 용량 상실이나 서버의 녹음 거절은 별도로 처리한다.

배포 도구의 기본값과 `--keep-warm`은 이 정책을 사용한다. 비용 절약을 위해 다시 0대로 줄이려면 배포 도구에 **`--scale-to-zero`**를 명시하고, 백엔드에는 **`STT_IDLE_SECONDS=300`**처럼 양수의 유휴 시간을 함께 설정해야 한다. CDK도 같은 환경변수를 읽는다. 두 설정 중 하나만 바꾸면 상시 유지와 자동 종료 정책이 충돌할 수 있다.

0대 모드에서는 활성 녹음·진행 중인 음성 처리가 모두 끝난 뒤 유휴 시간이 지나면 복제본을 0으로 내린다. 이후 `IDLE_RELEASE`가 빈 GPU 호스트를 반납한다. 별도의 `NoCapacityInvocationFailures > 0` 알람은 용량 없는 호출의 재기동을 요청한다. 이 모드의 기동 시간은 리전의 GPU 재고와 모델 로딩에 영향을 받는다.

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

- [Transcribe 스트리밍 요청의 언어 식별 옵션](https://docs.aws.amazon.com/transcribe/latest/APIReference/API_streaming_StartStreamTranscription.html)
- [AWS의 Qwen3-ASR JumpStart 출시 공지](https://aws.amazon.com/about-aws/whats-new/2026/05/speech-models-on-sagemaker-jumpstart/)
- [Qwen3-ASR-1.7B 모델 카드](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)
- [Nemotron 3.5 ASR 모델 카드](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
- [FLEURS 데이터셋](https://huggingface.co/datasets/google/fleurs)
- [SageMaker: Scale an endpoint to zero instances](https://docs.aws.amazon.com/sagemaker/latest/dg/endpoint-auto-scaling-zero-instances.html)
- [SageMaker: Heterogeneous instance pools](https://docs.aws.amazon.com/sagemaker/latest/dg/realtime-endpoints-heterogeneous.html)
- [SageMaker: 양방향 스트리밍 컨테이너 계약](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-inference-code.html)
- [SageMaker: GPU 드라이버와 Inference AMI 호환성](https://docs.aws.amazon.com/sagemaker/latest/dg/inference-gpu-drivers.html)

## 엔드포인트 배포와 앱 설정

배포용 환경에는 SageMaker Python SDK v2가 필요하다. 이 작업에서는 `2.257.1`을 사용했다. 백엔드 런타임에는 SDK를 추가로 설치하지 않아도 된다.

Qwen 모델은 `transmeet-qwen3-asr-model-offline` 이름으로 생성하며 네트워크 격리를 유지한다. 모델 아티팩트의 `requirements.txt`는 함께 제공되는 wheel 3개의 절대 경로를 가리키고, 전이 의존성은 기본 서빙 이미지에 이미 포함되어 있다. 두 배포 도구는 아래 환경변수로 패키지 인덱스 조회와 추가 의존성 설치를 막는다. 서빙 프로세스가 `pip install`을 앞에 붙이므로 값에 `install`을 넣지 않는다.

```text
STANDARD_PIP_ARGS=--no-deps --no-index -r /opt/ml/model/requirements.txt
```

JumpStart SDK는 지정한 환경변수를 우선하고 나머지 카탈로그 기본값을 추가한다. 계획 출력에서 모델 이름과 환경변수 재정의를 확인할 수 있다. 기존 SageMaker 모델의 환경변수는 변경할 수 없으므로, 재사용할 Qwen 모델에 위 설정이 없거나 다르면 배포 전에 중단한다. 올바른 설정으로 생성한 별도 모델은 `--model-name`으로 선택할 수 있다. Nemotron에는 이 재정의를 적용하지 않는다.

먼저 계획을 조회한다. `--apply`가 없는 명령은 리소스를 만들지 않는다.

```bash
python backend/scripts/deploy_stt_scaling.py qwen3-asr --region ap-northeast-2 --role "$SAGEMAKER_ROLE_ARN"
python backend/scripts/deploy_stt_scaling.py nemotron --region ap-northeast-2 --role "$SAGEMAKER_ROLE_ARN"
```

실제 배포는 SageMaker 실행 역할을 지정한다. 리소스는 삭제하지 않는다. 기존 엔드포인트의 구성 변경에는 명시적인 `--migrate`가 필요하다.

```bash
python backend/scripts/deploy_stt_scaling.py qwen3-asr \
  --region ap-northeast-2 --apply --wait --role "$SAGEMAKER_ROLE_ARN"
python backend/scripts/deploy_stt_scaling.py nemotron \
  --region ap-northeast-2 --apply --wait --role "$SAGEMAKER_ROLE_ARN"
```

기본 엔드포인트 이름은 `transmeet-qwen3-asr`, `transmeet-nemotron-asr`다. 컴포넌트 이름은 `transmeet-qwen3-asr-ic-v2`, `transmeet-nemotron-asr-ic-v2`다. Qwen의 상시 유지 모드는 이전에 동작한 `ml.g6.2xlarge`를 우선하고 `ml.g6.xlarge`를 대체 풀로 사용한다. 0대 모드는 이 우선순위를 반대로 사용한다. 스크립트는 리전의 모델 메타데이터로 지원 여부를 검사한다.

```bash
export QWEN_ASR_ENDPOINT=transmeet-qwen3-asr
export NEMOTRON_ASR_ENDPOINT=transmeet-nemotron-asr
export QWEN_ASR_INFERENCE_COMPONENT=transmeet-qwen3-asr-ic-v2
export NEMOTRON_ASR_INFERENCE_COMPONENT=transmeet-nemotron-asr-ic-v2
export QWEN_ASR_REGION=ap-northeast-2
export NEMOTRON_ASR_REGION=ap-northeast-2
export STT_IDLE_SECONDS=0
```

로컬 백엔드는 환경변수 또는 `.env`에서 읽는다. CDK는 같은 환경변수 또는 `qwenAsrEndpoint`, `nemotronAsrEndpoint`, `qwenAsrComponent`, `nemotronAsrComponent`, `qwenAsrRegion`, `nemotronAsrRegion` 컨텍스트를 읽어 ECS에 반영한다. CDK의 배치 엔진 기본 리전은 서울이며, 백엔드의 리전 필드가 비어 있으면 `REGION`으로 돌아간다. 실행 역할에는 해당 리전의 엔드포인트 조회·호출, 해당 컴포넌트 조회·복제본 수 변경, `TransMeet/STT` 네임스페이스의 지표 전송만 추가한다.

상태 조회와 유휴 지표도 모델의 리전을 사용한다. 별도의 Nemotron 스트리밍 엔드포인트를 사용할 때는 `NEMOTRON_STREAMING_REGION` / `nemotronStreamingRegion`으로 리전을 독립 지정한다. 이 값이 비어 있으면 `REGION`을 사용한다.

```bash
cd infra
npx cdk diff \
  -c qwenAsrEndpoint=transmeet-qwen3-asr \
  -c nemotronAsrEndpoint=transmeet-nemotron-asr
```

운영 반영에는 CDK 설정 변경, 백엔드 이미지 빌드·푸시와 ECS 재배포, 프런트엔드 배포가 필요하다. 기존 절차는 루트 README의 배포 섹션을 따른다. 엔드포인트를 설정한 환경변수나 CDK 컨텍스트는 이후 배포에도 유지해야 한다.

AWS Pricing API의 `USE1-Host:ml.g6.xlarge`, `USE1-Host:ml.g5.2xlarge` 조회 기준 인스턴스 비용은 각각 시간당 `$1.1267`, `$1.5150`이다(2026-10-28, 온디맨드). 두 개가 실행 중이면 합계 시간당 `$2.6417`이며 스토리지·지표·알람·전송·기존 Whisper·Transcribe 비용은 제외한다. Qwen 대체 풀인 `ml.g6.2xlarge`는 시간당 `$1.2220`이다. 실제 GPU 인스턴스가 0대인 동안 해당 GPU 호스팅 비용은 발생하지 않는다.

현재 서울 배포의 실제 호스트는 Qwen `ml.g6.2xlarge`(시간당 `$1.5026`), Nemotron `ml.g5.2xlarge`(시간당 `$1.8629`)다. 합계 시간당 `$3.3655`, 월 730시간이면 약 `$2,456.82`이며 기존 Whisper와 기타 비용은 별도다. [서울 Pricing API 원문](../benchmarks/stt-ko-en-2026-10-28/regional-pricing.json)에 근거를 보존했다.

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

# 현재 앱의 SageMaker 입력 길이에 가까운 6초 구간
python backend/scripts/benchmark_stt.py samples/manifest.json \
  --chunk-seconds 6 --output results/chunked.json
```

`--providers whisper transcribe qwen3-asr nemotron`, `--repeat 3`, `--timeout 90`을 지정할 수 있다. 자동 감지 비교는 매니페스트의 `language`를 `auto`로 설정한다.

측정 기준:

- **CER:** NFKC 정규화 후 대소문자·공백·문장부호 차이를 제거한 문자 오류율. 숫자와 한글 수사, 영문 약어와 발음 표기는 임의로 같은 것으로 바꾸지 않는다.
- **집계:** 샘플별 CER의 단순 평균이 아닌, 전체 오류 문자 수 ÷ 전체 정답 문자 수.
- **시간:** 이미 녹음된 구간의 호출 시작부터 최종 결과까지 걸린 시간. Transcribe에도 실시간 속도로 대기하지 않고 녹음을 전달한다. 실제 마이크 입력에서 자막이 나오는 지연 시간과는 다르다.
- **분할 모드:** 지정한 길이로 SageMaker 입력만 나누고 결과를 합친다. Transcribe는 앱과 같이 하나의 지속 세션을 사용한다. 앱의 무음·환각 필터와 번역 시간은 포함하지 않는다.
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

Qwen을 같은 음성으로 추가 측정한 결과는 전체 문장 2.68%, 6초 분할 5.82%, 2초 분할 19.69%였다. 이 결과를 바탕으로 SageMaker 엔진의 공통 기본값을 2초에서 6초로 변경했다. Nemotron의 6초 조건은 아직 별도로 측정하지 않았다.

고정된 시간 경계는 단어 중간을 자르고 문맥을 줄인다. 한국어를 명시하는 것만으로 이 차이가 해소되지 않았다. 6초로 늘려도 발화 경계를 보장하는 것은 아니므로, 발화 경계에 맞춘 버퍼링이나 모델의 지속 스트리밍은 추가 개선 대상이다.

Nemotron은 숫자를 `15` 대신 `십오`처럼 출력하기도 한다. 이 보고서의 CER은 그 표기 차이를 포함한다. 따라서 점수와 함께 JSON의 정답·인식 원문을 확인해야 한다.
