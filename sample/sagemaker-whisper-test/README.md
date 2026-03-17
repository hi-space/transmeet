# SageMaker Whisper Endpoint Tester

브라우저에서 마이크로 음성을 녹음하고 SageMaker Whisper 엔드포인트로 전송하여 실시간 전사 결과를 확인하는 단일 파일 웹 테스트 도구입니다.

## 동작 방식

1. 브라우저에서 마이크 녹음 (WebM/Opus)
2. 녹음 종료 시 오디오를 hex 인코딩하여 로컬 서버(`/invoke`)로 전송
3. 서버에서 ffmpeg으로 WebM → 16kHz mono WAV 변환
4. 변환된 WAV를 SageMaker `whisper-large` 엔드포인트에 전달
5. 전사 결과를 브라우저에 JSON으로 표시

## 사전 요구사항

- Python 3.8+
- `boto3` (`pip install boto3`)
- `ffmpeg` (시스템에 설치되어 있어야 함)
- SageMaker에 `whisper-large` 엔드포인트가 배포되어 있어야 함
- AWS 자격 증명이 설정되어 있어야 함 (us-east-1 리전)

## 실행

```bash
python app.py
```

`http://localhost:8080`으로 접속하여 마이크 버튼을 클릭하면 녹음이 시작됩니다.

## 설정 변경

`app.py` 상단의 상수를 수정합니다:

```python
REGION = "us-east-1"
ENDPOINT_NAME = "whisper-large"
```

## 지원 파라미터

웹 UI의 Parameters 섹션에서 다음 항목을 조정할 수 있습니다:

| 파라미터 | 설명 |
|---|---|
| language | 음성 언어 (auto detect / english / korean 등) |
| task | transcribe (원본 언어) 또는 translate (영어 번역) |
| max_new_tokens | 최대 생성 토큰 수 (기본 444) |
| num_beams | 빔 서치 너비 (1 = greedy) |
| temperature | 샘플링 온도 (0 = 결정적) |
| do_sample / top_p / top_k | 샘플링 관련 옵션 |
