# Fresh four-provider streaming comparison

Run: 2026-10-28T23:40:26.467445+00:00 to 2026-10-28T23:44:22.267904+00:00. All 80 results are newly measured after deployment verification; 20 identical audio hashes were checked across all providers. Endpoint configurations, images and hardware were unchanged before/after.

| Provider | KO CER↓ | EN WER↓ | First caption | First partial | Changed-caption interval median/P90 | Final lag | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|
| transcribe | 8.72% | 6.64% | 2.634s | 2.634s | 0.508/0.594s | 0.164s | 0 |
| whisper | 4.25% | 3.79% | 1.539s | 1.539s | 1.127/1.259s | 0.511s | 0 |
| qwen3-asr | 4.25% | 3.79% | 1.215s | 1.223s | 0.993/1.172s | 0.403s | 0 |
| nemotron | 8.95% | 7.11% | 1.806s | 1.806s | 0.004/0.630s | 0.052s | 0 |

All timing values are medians except the labeled P90. Caption timing begins **after adapter ready**. Final lag uses the complete source-file EOF, including trailing silence, so negative values are valid. Caption update intervals include token bursts and pauses; a low token-event interval is not a display-refresh rate. First caption does not assert correctness.

Whisper now uses the real bidirectional streaming adapter with 1000 ms updates and Silero VAD. It periodically re-decodes a bounded utterance; it is not a cache-aware acoustic streaming decoder. Qwen uses 1000 ms chunks, Nemotron 560 ms, and all providers receive 160 ms PCM capture frames.

## Whisper versus the historical six-second batch run

| Metric | Historical batch | Current streaming |
|---|---:|---:|
| First caption median | 7.975s | 1.539s |
| First partial median | — | 1.539s |
| Korean CER | 12.08% | 4.25% |
| English WER | 6.64% | 3.79% |
| Final lag median | 1.985s | 0.511s |
| Hardware | ml.g5.2xlarge | ml.g5.xlarge |

Full serving-path comparison: 6-second batch windows on ml.g5.2xlarge versus periodic bounded-utterance streaming plus Silero VAD on ml.g5.xlarge. Hardware, container/decoder, VAD and framing changed; do not attribute all differences to streaming alone.

Whisper and Qwen tie in total error counts on this corpus (19/447 Korean characters and 8/211 English words), but their per-clip errors and raw hypotheses differ.

The historical 20 Whisper results are used only in this before/after section; none were reused in the fresh 80-result comparison. `--whisper-mode batch` preserves six-second framing on the currently configured endpoint but does not recreate the old image/hardware.

## Setup and application timing

| Provider | Ready/setup median | End-to-end median | Region | Hardware |
|---|---:|---:|---|---|
| transcribe | 0.811s | 11.080s | us-east-1 | AWS managed; no customer SageMaker endpoint |
| whisper | 1.082s | 11.067s | us-east-1 | ml.g5.xlarge |
| qwen3-asr | 0.329s | 10.853s | ap-northeast-2 | ml.g6.xlarge |
| nemotron | 0.322s | 10.813s | ap-northeast-2 | ml.g5.xlarge |

A separate authenticated production application probe measured Whisper's first partial at **2.753s from recording start** and verified final persistence. That is one application-path check, not the post-ready 20-clip median above. Main benchmark timings exclude application setup, frontend rendering, translation and persistence.

## Evidence and limits

The eight provider/language JSON files retain every raw timestamped event, reference, final hypothesis, score count, parameter and source hash. `run.json` records commands, environment, SDK versions and harness hash. `endpoints-before.json` and `endpoints-after.json` record actual model/image/hardware identity. `verification.json` records recomputed scores and hash checks.

This small read-speech corpus does not establish results for overlapping, spontaneous meetings or every microphone/noise condition. Providers use different deployed hardware/regions, so the table compares serving paths rather than equal-hardware model efficiency.
