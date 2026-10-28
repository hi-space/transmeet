# Qwen Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> or superpowers:subagent-driven-development. Track task completion below.

**Goal:** Run Qwen meeting recognition through a persistent streaming session
with live partial subtitles in Korean and English.

**Architecture:** A custom Seoul SageMaker container hosts the official Qwen
vLLM streaming API. Reuse the application's bounded bidirectional transport,
partial/final subtitle pipeline, and short browser audio frames.

**Tech Stack:** Python, FastAPI, Qwen-ASR/vLLM, SageMaker HTTP/2 SDK,
Next.js/React/TypeScript, existing AWS infrastructure.

**Spec:** `docs/superpowers/specs/2026-10-28-qwen-streaming-design.md`

## Global Constraints

- Preserve Qwen3-ASR 1.7B, Korean/English/AUTO, and all other providers.
- Seoul preferred, Oregon fallback; one warm Qwen GPU after cutover.
- Browser audio 160 ms; model chunks 1000/2000 ms; endpointing silence 800 ms,
  maximum utterance 15 seconds.
- Partial results are replaceable UI state; only unique final results enter
  translation/persistence.
- Keep queues/session state bounded and flush final audio before rotating.
- Work in the shared checkout with a before snapshot of touched files, because
  existing live provider integration and concurrent deployment changes must
  remain visible. Do not commit or publish unrelated changes.

## Review Focus

1. The last sub-second utterance at stop must not disappear.
2. A final from an old recording must not clear a new recording's partial.
3. Concurrent clients must not share hypotheses or corrupt synchronous vLLM.
4. Silence and prolonged speech must not accumulate unbounded audio.
5. Frontend and backend capability skew must retain the correct capture mode.

## Task 1: Qwen inference runtime and deployment

**Files:** Create `inference/qwen_streaming/`,
`backend/scripts/deploy_qwen_streaming.py`,
`backend/tests/test_qwen_engine.py`, `backend/tests/test_qwen_server.py`,
`backend/tests/test_qwen_streaming_deploy.py`.

**Interfaces:** Same Nemotron event contract; `start_utterance(language,
chunk_ms,on_partial)` returns a handle exposing `feed(pcm)`, async `finish()`
and async `cancel()`. `make_plan(image,role,region,endpoint,instance)` returns
reviewable SageMaker model/endpoint configuration.

- [x] Write failing tests for state continuity, partial before final,
  short-tail flush, UTF-8/Korean preservation, independent sessions,
  bounded audio and cancellation.
- [x] Run focused tests and observe feature failures.
- [x] Implement official streaming adapter, bounded endpointing/runtime,
  WebSocket contract and pinned container.
- [x] Write/run deployment tests for regional artifact, GPU AMI, immutable
  identity, one warm instance and endpoint rollback detection.
- [x] Build the image, validate container imports/contracts, deploy a separately
  named Qwen streaming endpoint, and prove a real inference session.

## Task 2: Application integration

**Files:** Modify `backend/app/nemotron_stream.py`,
`backend/app/ws_handler.py`, `backend/app/stt.py`, `backend/app/config.py`,
`backend/app/stt_capacity.py` only if needed, `src/lib/sttProviders.ts`,
`src/app/page.tsx`, `src/components/SettingsPanel.tsx`, and focused tests.

**Interfaces:** Config gains `QWEN_STREAMING_ENABLED`, `_ENDPOINT`, `_REGION`,
`_CHUNK_MS`; Qwen protocol/start settings are as in the spec. Existing backend
events use `sttProvider:"qwen3-asr"` and recording/utterance identifiers.

- [x] Add failing backend tests for Qwen configuration, regional routing,
  partial/final flow, provider metadata, duplicate finals and stopped sessions.
- [x] Run the tests, then parameterize existing streaming transport and
  subtitle handling while preserving Nemotron call compatibility.
- [x] Add failing frontend tests for advertised Qwen streaming vs old backend,
  160 ms capture and partial/final identity.
- [x] Implement capability-driven UI/capture and final-only translation.
- [x] Verify focused tests, full backend suite, frontend tests and TypeScript.

## Task 3: Live measurements and cutover

**Files:** Create `backend/scripts/benchmark_qwen_streaming.py`,
`benchmarks/qwen-streaming-2026-10-28/`; modify `.env.example`,
`infra/lib/transmeet-stack.ts`, `docs/stt-models.md`.

**Interfaces:** Reuse the existing FLEURS Korean/English WAVs and references;
benchmark feeds audio at real-time speed and records event arrival times.

- [x] Add meaningful benchmark scoring/pacing tests; compare 1000/2000 ms
  model chunks and retain raw events, errors and input provenance.
- [x] Run an independent review and fix material findings with regression tests.
- [x] Add scoped regional invocation IAM and backend configuration; build a
  versioned image from the current live code plus this task's changes.
- [x] Deploy backend/frontend, preserving any newer unrelated live revision.
- [x] Exercise actual WebSocket/browser path; verify recording, partials,
  final persistence and compatibility with other providers.
- [x] After successful cutover, set the old Qwen batch copy/host to zero and
  retain one warm streaming instance. Record live configuration and cost.
- [x] Save measurements, implementation limitations and verification evidence.

## Execution record

Plan prepared before product code. The user's explicit streaming instruction
authorizes implementation and the previously approved regional/keep-warm
deployment. A fresh design review precedes implementation.


### Verified progress and rulings

- Design builder review caught official finalizer UTF-8 rollback; actual Qwen
  tokenizer regression now covers it.
- Independent code review caught the missing Qwen `streaming` capability field
  and incorrect inherited artifact version. Both were reproduced RED, fixed,
  and verified GREEN.
- Browser testing caught the batch-only forced-AUTO language rule. Streaming
  capability now enables Korean/English selection in the header and recording
  request; old batch behavior is retained. Unit RED→GREEN and18 browser
  scenarios passed, including16/44.1/48kHz tails and late final ordering.
- Backend image built from actual live rev28 image with a scoped five-file patch,
  smoke tested and pushed; image metadata is in `/tmp/transmeet-qwen-backend-image.json`.
- Frontend production build passed lint/types and was uploaded; CDN invalidation
  completed. Backend activation is waiting for corrected real speech results.
- Ruling: actual initial English probe revealed quiet speech discarded by RMS0.01.
  A single lower threshold also lost speech in other files. Retain all nonzero
  audio and use relative energy only for boundaries. All20 original WAVs passed
  byte-for-byte retention, and digital silence/short tails have regressions.
  This intentionally allows uncertain room tone into bounded ASR utterances.
- Ruling: update the provisional streaming endpoint to g6.xlarge (same L4/24GB,
  4vCPU/16GiB). Measured peak host memory was~4GB and CPU~1 core on g6.2xlarge.
  This avoids its exhausted instance-type quota during the update and lowers
  steady Qwen cost to1.3854USD/hour; real GPU checks still gate activation.
- Original failed-quality smoke results are preserved, not replaced by retries.


### Completion

Actual final deployment preserved concurrent message-table storage changes, honored selected streaming language, and enabled both Seoul native engines on ECS revision35. Runtime smoke passed before the user-requested fresh all4-provider comparison (21:06:23–21:10:30UTC).80/80results,zeroerrors;Qwenbestaccuracy/firstcaptiononthissmallcorpus. NativeGPUeach1;oldQwencompute/componentremovedbecausepool/quotalimitsblockedzero-hostmigration,model/configretained;Nemoldbatchhost/copies0. Testfixturesremoved. Full evidence inbenchmarks/stt-streaming-comparison-2026-10-28.
