# Whisper Streaming Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans or
> superpowers:subagent-driven-development. Independent inference and application
> tasks may use superpowers:dispatching-parallel-agents with disjoint files.

**Goal:** Add and deploy live Whisper meeting subtitles on SageMaker using the
existing JumpStart large-v3-turbo weights.

**Architecture:** A dual-protocol custom inference container periodically
decodes bounded utterances and serves both existing batch requests and the
shared bidirectional stream. The app uses capability-driven audio capture and
final-only translation/persistence.

**Tech Stack:** Python, PyTorch/Transformers, FastAPI, SageMaker HTTP/2 SDK,
Next.js/TypeScript and existing AWS deployment tooling.

**Spec:** `docs/superpowers/specs/2026-10-28-whisper-streaming-design.md`

## Global Constraints

- Preserve JumpStart Whisper large-v3-turbo artifact v1.0.0 in us-east-1.
- Browser frames 160 ms; model intervals 1000/2000 ms; initial endpointing
  800 ms relative silence or 15 seconds.
- Partial text is replaceable; only unique finals are translated and stored.
- Bound queues, utterance duration and concurrency; preserve final tails.
- Maintain existing Whisper batch API and all other provider behavior.
- Keep one production Whisper GPU after validation; preserve old model/config
  for rollback. No unrelated cloud resources or live settings may be replaced.
- Preserve before-snapshot changes in the shared checkout. Only task-owned
  temporary resources can be deleted automatically.

## Review Focus

1. A sub-second final tail must survive stop and stream rotation.
2. An old recording's final must not clear a newer recording's partial.
3. Slow GPU inference must coalesce work without dropping accepted audio.
4. Quiet Korean speech must not be removed by an absolute amplitude gate.
5. Older frontends/backends and batch clients must remain compatible.

## Task 1: Inference runtime and protocol

**Files:** Create `inference/whisper_streaming/`,
`backend/tests/test_whisper_engine.py`,
`backend/tests/test_whisper_server.py`.

**Interfaces:** `ModelRuntime.start_utterance(language, chunk_ms, on_partial)`
returns `feed(pcm)`, async `finish() -> (text, language)`, async `cancel()`.
`create_app(runtime=None)` exposes `/ping`, `/invocations`, and the shared
bidirectional WebSocket event contract.

- [x] Write failing tests for early partials, revisions, tail flush, bounded
  audio, independent sessions, inference coalescing and cancellation.
- [x] Implement periodic Whisper decoding with pinned, offline shared weights.
- [x] Test endpointing, malformed PCM, errors and short batch/stream requests.
- [x] Implement dual-protocol server, health check and pinned container.
- [x] Run focused inference/protocol tests and record results.

## Task 2: Application integration

**Files:** Modify `backend/app/{config,stt,stt_capacity,nemotron_stream,ws_handler}.py`,
`src/lib/sttProviders.ts`, `src/app/page.tsx`,
`src/components/SettingsPanel.tsx`, and corresponding backend/frontend tests.

**Interfaces:** `WhisperStream` uses existing transport with 1000/2000 ms
intervals. Config adds `WHISPER_STREAMING_*`. Status advertises `streaming:true`
only for configured streaming; disabled legacy Whisper retains batch semantics.

- [x] Write failing capability, routing, partial/final and deduplication tests.
- [x] Add the transport/config/routing and validate configured readiness.
- [x] Test and implement 160 ms capture, final-only translation, language and
  settings behavior, preserving batch compatibility.
- [x] Run focused app tests, full backend suite and frontend tests.

## Task 3: Deployment tooling and measurements

**Files:** Create `backend/scripts/deploy_whisper_streaming.py`,
`backend/scripts/benchmark_whisper_streaming.py`, related tests, and
`benchmarks/whisper-streaming-2026-10-28/`. Modify `.env.example`,
`infra/lib/transmeet-stack.ts`, and `docs/stt-models.md`.

**Interfaces:** `make_plan` returns immutable-image, regional SageMaker
model/endpoint specs. Default plans are read-only; applying/updating is explicit.
Benchmark uses `WhisperStream` and the existing FLEURS manifests/scoring.

- [x] Verify current quota, price, GPU AMI and dependency/checkpoint compatibility.
- [x] Write failing scoped deployment and benchmark identity/pacing tests.
- [x] Implement plans, config/rollback checks and reproducible benchmark wrapper.
- [x] Build image; run imports, health, dependency and scoped-IAM checks; deploy staging.
- [x] Run Korean/English live-paced 1000/2000 ms comparisons and batch probes.

## Task 4: Review, production activation and evidence

- [x] Run a fresh independent code review; fix significant findings with tests.
- [x] Run backend/frontend suites, TypeScript, build and infrastructure checks.
- [x] Snapshot live ECS revision/configuration and endpoint before activation.
- [x] Update only the verified Whisper endpoint and necessary app changes;
  preserve/rebase on any concurrent live changes.
- [x] Exercise real application WebSocket path and browser partial/final display.
- [x] Confirm one production Whisper GPU, retain rollback model/config, and
  remove only task-created staging resources.
- [x] Record measurements, deployment identities, limitations and verification.

## Execution ledger

- Plan prepared before implementation. The user explicitly selected the existing
  SageMaker streaming architecture for Whisper. A fresh builder review precedes
  implementation; no additional product-choice approval is required.
- Work is on `codex/whisper-streaming`; pre-existing touched files and the full
  initial diff are saved under `/tmp/transmeet-whisper-streaming-before/`.

### Verification before cloud rollout

- Full backend suite: 567 passed (three pre-existing Pydantic deprecation warnings).
- All frontend unit tests: 121 passed; production Next.js build and infrastructure TypeScript check passed.
- Runtime/protocol focused tests:45 passed; deployment/benchmark tests:8 passed.
- Independent review reproduced a legacy-client compatibility defect: global enable could route a6-second legacy Whisper request into the streaming queue. Ruling: require explicit per-recording `sttStreaming: true` for Whisper; preserve batch for absent/false. Regression work is in progress before deployment.
- Live backend revision35 differs from the checkout in unrelated configuration and summary behavior. The deployment overlay applies only the Whisper diff to that live source.

- Compatibility fix verified: explicit Whisper opt-in; absent/false/malformed keeps batch, including6-second old-client requests. Final full backend run572 passed; all frontend unit tests121 passed; six browser scenarios passed; production frontend build passed.
- Pinned container smoke passed with real Torch2.1.0/CUDA11.8 and Transformers4.39.3: HTTP health, batch route, WebSocket protocol; pip check reported no broken requirements. Neural GPU inference remains gated on staging readiness.
- Production overlay tests51 passed against the actual live backend source. New image digest is in /tmp/transmeet-whisper-streaming-work/backend-image.json.
- Scoped Whisper IAM permission added; individual simulations for InvokeEndpoint, InvokeEndpointWithBidirectionalStream, and DescribeEndpoint all returned allowed. The combined simulator request rejects these API action families together, so they were verified separately.

- Actual GPU smoke: Korean and English produce partials at~1.5s and main transcripts match old batch, but both append a hallucinated thanks on fresh trailing room tone. Production activation paused at the quality gate; initial raw data/image retained in baseline-room-tone. Qwen's retain-nonzero policy is insufficient for Whisper. Runtime worker is adding model no-speech handling after inspecting all20 audio files; no phrase blacklist or absolute quiet-speech gate.

- V2 no-speech confidence experiment failed the acoustic gate: p_no_speech rounded to0 for every real-model input including silence, so false thanks persisted. Eight actual normal/quiet acknowledgements remained correct. Saved full v2 baseline and diagnostics under baseline-nospeech-probability. Investigating explicit acoustic VAD locally before any third image. No production model/backend activation has occurred.

- V3 real GPU validation passed: Silero VAD removed false trailing acknowledgements, while all8 normal/quiet actual short responses retained CER0. Korean/English fixed and auto recognition plus18.9s batch compatibility passed.
- V3 full backend626 passed with only3 existing Pydantic warnings; focused VAD code review found no new Important/Critical issues.
- Final image sha256:365fd9234b8ad353937a45fd3660429cd6b354d8bd56192fb0f35312c31f35a5. Production endpoint update started only after the above checks and a20-file composed quality check; full1000/2000ms streamed comparisons run during its rollout. Old backend still selects the compatible batch route until explicit activation.
- Immutable frontend static assets staged; entry HTML has not been changed. Task-owned test account uses existing SRP auth and suppressed invitation; clean it up after final app probes.

- Production G5.2xlarge rollout reverted to the original healthy configuration with AWS InsufficientInstanceCapacity. No app activation happened. Ruling: retry with the already benchmarked G5.xlarge, which lowers the verified hourly rate to1.408USD and preserves one-GPU design. Failure metadata preserved under capacity-fallback. New single-owner controller is /tmp/transmeet-whisper-streaming-work/rollout_retry.py (exec54495); original controllers have exited.

### Completed deployment

- Verified2026-10-28T23:13Z: whisper-large InService on one ml.g5.xlarge; current configuration whisper-large-f07ce2984867-cfg.
- ECS backend revision36 stable; Whisper streaming enabled with1000ms model intervals. Qwen and Nemotron streaming settings preserved.
- Frontend published and CloudFront invalidation completed; public index bytes match the verified build.
- Authenticated production app probe: live partial at2.75s, final English translation and one persisted message. Legacy6-second request also persisted one message without streaming partials.
- Task-created staging endpoint and all three stage model/config versions deleted. Temporary Cognito test account and local credentials removed. Original production model/config retained for rollback.
- Final evidence: benchmarks/whisper-streaming-2026-10-28/deployment.json, application-stream.json, application-legacy.json, cleanup.json and measurements.json.
- Infrastructure default now advertises the deployed Whisper streaming endpoint. Final infrastructure TypeScript and git diff whitespace checks passed. No unrelated changes were reverted or committed.
