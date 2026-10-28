# Fresh four-provider real-time comparison

Run: 2026-10-28T21:06:23.940839+00:00 to 2026-10-28T21:10:30.714347+00:00. All 80 clip/provider results are fresh, after deployment verification. The same 20 audio hashes were verified for every provider.

| Provider | KO CER ↓ | EN WER ↓ | First caption | First partial | Changed-caption interval median / P90 | Final lag | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|
| transcribe | 8.72% | 6.64% | 2.724 s | 2.724 s | 0.507 / 0.587 s | 0.242 s | 0 |
| whisper | 12.08% | 6.64% | 7.975 s | — s | 4.564 / 6.048 s | 1.985 s | 0 |
| qwen3-asr | 4.25% | 3.79% | 1.221 s | 1.229 s | 0.993 / 1.166 s | 0.415 s | 0 |
| nemotron | 8.95% | 7.11% | 1.806 s | 1.806 s | 0.004 / 0.630 s | 0.052 s | 0 |

Timing values are medians across the same 20 clips, except the explicitly labeled interval P90. First caption is the first nonempty partial **or** final. It does not assert that the first hypothesis is already correct. Final lag is measured against the complete source-file EOF, including trailing silence; negative values are valid. Changed-caption intervals are pooled within clips and can reflect token bursts.

Whisper uses serial six-second request/response windows and flushes the last short window. It emits finals only. Qwen uses 1,000 ms native model chunks; Nemotron uses 560 ms. All capture frames are paced at 160 ms.

## Setup and the actual application

| Provider | API/adapter setup median | End-to-end median | Region | Hardware |
|---|---:|---:|---|---|
| transcribe | 0.795 s | 11.054 s | us-east-1 | AWS managed; not exposed as a customer SageMaker instance |
| whisper | 0.000 s | 12.560 s | us-east-1 | ml.g5.2xlarge |
| qwen3-asr | 0.324 s | 10.851 s | ap-northeast-2 | ml.g6.xlarge |
| nemotron | 0.321 s | 10.809 s | ap-northeast-2 | ml.g5.xlarge |

The main benchmark starts the capture clock **after ready**. Whisper ready means its capture buffer is initialized; endpoint readiness is established by preflight. Actual authenticated application checks included the recording-start connection/worker setup:
- qwen3-asr: ko 3.424 s, en 3.379 s.
- nemotron: ko 3.582 s, en 3.480 s.

Those application values are separate checks and must not be replaced with the lower post-ready model/API numbers. They also verified translation and persistence, which are outside this benchmark’s timed scope.

## Evidence and limits

- Per-provider/language JSON files retain all raw timestamped events, references, hypotheses, score counts, input hashes, actual parameters, failures, and null metrics.
- `run.json` records exact commands, interpreter/SDK information, source hash, verification hashes, and run timestamps. `summary.json` contains the aggregate values and input cross-check.
- The preflight report records actual image/artifact/hardware identities. Regions and hardware are intentionally those of the live application.
- This small read-speech corpus does not establish accuracy for spontaneous meetings, overlapping speakers, or every microphone/noise condition.

## 최종 운영 상태

Qwen과 Nemotron은 서울 스트리밍 GPU를 각각1대 유지한다. 사용하지 않는 Qwen 배치 엔드포인트·컴포넌트는 제거했으며 모델·설정은 보관했다. Nemotron 배치는 복제본0·호스트0으로 내려갔다. [최종 호스트 상태](../final-host-state.json), [앱 검증](../deployment-live-smoke.json), [검증 정리](../completion-verification.json)에 기록했다. 테스트용 계정·회의·오디오는 제거했다.
