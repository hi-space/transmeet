# JumpStart STT options implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task. Preserve the pre-existing workspace edits.

**Goal:** Add Qwen3-ASR-1.7B and Nemotron 3.5 ASR 0.6B alongside Whisper and Transcribe, and compare the engines using identical Korean audio.

**Architecture:** Keep Transcribe's persistent streaming session. Route the other three providers through the existing buffered audio pipeline and a small SageMaker request/response adapter. Use the same adapter in a reproducible benchmark command.

**Tech Stack:** Next.js/TypeScript, FastAPI/Python, aiobotocore/boto3, AWS CDK, SageMaker JumpStart.

**Spec:** User-approved scope in this conversation: “둘다 추가. 비교도 해보고”. Both providers are additional choices; the existing default remains Transcribe. The first integration uses utterance inference, not stateful model streaming.

**Superseding user direction:** “정리하지말고 그대로 냅둬. 평상시 0으로 뒀다가 사용할때만 올리면 되지 않아? 그리고 설정에서 선택가능하게 해야하잖아”. Do not delete further endpoints. Retain models/endpoints, use Inference Components for zero idle GPU capacity, expose preparation/readiness in settings and the recording control, and deploy the working application. Nemotron's temporary endpoint was already deleted before this direction and must be recreated.

**Current execution checkpoint:** Backend 171 tests, TypeScript, and the production frontend build passed. Eight controlled real-browser checks passed (saved selection does not wake; no microphone while warming; disabled warm button; transient status preserves recording; selection prepares/persists; mobile layout; microphone-denial creates no session). Backend image `stt-zero-20261028` has been pushed; digest `sha256:ea04b2809517731d9ccd8455ef6e265359ed6055b38f7e9ee70e3aab58bb935c`. CDK deployment is running with `-c wsImageTag=stt-zero-20261028`; log `/tmp/transmeet-stt-cdk-deploy.log`. Frontend artifact is `/tmp/transmeet-stt-production-build/out` and is not yet uploaded.

**Cloud checkpoint:** Retained endpoints `transmeet-qwen3-asr` and `transmeet-nemotron-asr` are being updated to `*-zero-v3` (network isolation enabled). Coordinator output is in `/tmp/transmeet-stt-comparison-20261028/current-*-setup.log`. It creates `*-ic`, configures autoscaling, keeps Qwen warm for comparison, and requests zero copies for Nemotron. Prior Qwen comparison endpoints both failed due to insufficient capacity and remain preserved. Do not delete any remaining endpoint/model/config.

**Verified service constraints:** Initial component copy count must be 1 in the live service despite the SDK minimum of 0. IC endpoint configs must omit `InitialVariantWeight`. Endpoint and model network-isolation settings must match. Updating a zero-instance IC endpoint requires `RetainAllVariantProperties=True`. The deployment helper implements these constraints.

## Global constraints

- Preserve unrelated uncommitted work, including the current settings UI and meeting-end changes.
- Verified region: `us-east-1`; confirmed 2026-10-28; recheck model metadata after 2026-11-11.
- Qwen model: `huggingface-asr-qwen3-asr-1-7b`, version `1.0.2`, default `ml.g6.xlarge`.
- Nemotron model: `huggingface-asr-nvidia-nemotron-3-5-asr-streaming-0-6b`, version `1.0.2`, default `ml.g5.2xlarge`.
- Do not silently fall back to Whisper when a new provider is unavailable.
- Report local validation, live inference, and quality measurements separately.
- Benchmark ground truth must come from a transcript supplied with the audio, not from another recognizer.
- No claim about real meeting accuracy from synthetic speech alone.
- Preserve all remaining endpoints. Earlier cleanup notes below describe history, not current authorization.
- GPU capacity must reach zero when idle while the endpoint and component remain.
- Do not start microphone capture while a selected managed engine is unavailable or warming.

## Review focus

- Unsupported or stale saved providers must resolve predictably.
- Korean and automatic language modes must not use Nemotron's implicit English default.
- Model language tags must not appear in subtitles or translation input.
- Pending inference must not be cancelled by the next audio chunk or change providers halfway through an utterance.
- Unconfigured endpoints and malformed responses must be visible failures, not empty successful results.

## Task 1: Provider adapters and WebSocket integration

**Files:** `backend/app/stt.py`, `backend/app/config.py`, `backend/app/ws_handler.py`, `backend/tests/test_stt.py`.

**Interfaces:** `build_request(provider, audio, source_lang)` supplies SageMaker request arguments; `parse_response(provider, payload)` returns transcription and detected language; `transcribe_audio(provider, audio, source_lang)` invokes the configured endpoint asynchronously.

- [x] Test request formats, language controls, output tags, empty audio, malformed responses, and missing configuration.
- [x] Run the new tests and observe the missing behavior.
- [x] Add provider-specific adapters and keep common filtering/translation in the WebSocket handler.
- [x] Exercise final buffered audio and in-flight inference lifecycle.
- [x] Run the complete backend test suite.

## Task 2: Settings and deployment configuration

**Files:** `src/lib/sttProviders.ts`, `src/hooks/useSettings.ts`, `src/components/SettingsPanel.tsx`, `src/app/page.tsx`, `.env.example`, `infra/lib/transmeet-stack.ts`, `tests/sttProviders.test.mjs`.

**Interfaces:** A shared provider catalogue defines IDs, display names, normalization, and audio chunk duration. Endpoint names are operator configuration, never client input.

- [x] Test saved provider normalization and chunk duration selection.
- [x] Add both choices using the existing settings controls.
- [x] Configure optional endpoint environment variables and narrowly scoped invoke permissions.
- [x] Check TypeScript, frontend tests, and CDK synthesis/type validation.

## Task 3: Reproducible comparison and operator documentation

**Files:** `backend/scripts/benchmark_stt.py`, `backend/scripts/deploy_stt.py`, `backend/tests/test_stt_benchmark.py`, `docs/stt-models.md`, `README.md`.

**Interfaces:** The benchmark consumes a manifest of local WAV paths, language codes, and human transcripts. It writes per-sample transcripts, CER, elapsed time, and errors; aggregate scores are weighted by reference characters. A preparation command describes/deploys the two pinned JumpStart models explicitly.

- [x] Test Unicode normalization, edit distance, sample weighting, and error reporting.
- [x] Add manifest-based comparisons for SageMaker providers and Transcribe.
- [ ] Validate official Korean sample provenance and measure live engines if endpoints are available.
- [ ] Document model availability, connection configuration, measured results, and limitations.
- [ ] Request a fresh code review, resolve material findings, and rerun affected checks.

## Execution notes

- Initial workspace: `main`, ahead of origin by one commit, with unrelated edits already present. Work in place without staging or committing those edits; retain a baseline copy of touched files for review.
- Baseline frontend tests: 14 passed.
- Live account initially contains `whisper-large` (Whisper large-v3-turbo on `ml.g5.2xlarge`); neither new endpoint is deployed.
- Backend verification after integration: 154 passed, including concurrent diarization changes; 31 STT/benchmark tests passed.
- Fresh review found two transition issues. Fixed by snapshotting queued Transcribe finals, draining them before a new session, and flushing browser audio before `startRecording`.
- Ruling: preserve Transcribe's existing asynchronous stop/translation behavior; drain translation tasks on provider restart rather than inside ordinary stop, to preserve meeting-end behavior.
- Browser check at 390px: four options selectable and persisted, no horizontal overflow. Actual capture with mocked transport: a pending 26,792-byte WAV was sent immediately before switching Qwen to Nemotron.
- Comparison inputs: FLEURS `ko_kr/test`, rows 0–9, 10 clips, 111.12 seconds, 447 normalized reference characters. Temporary inputs/results: `/tmp/transmeet-stt-comparison-20261028`.
- Baseline full-clip CER: Whisper 16/447 (3.58%), Transcribe 12/447 (2.68%). Whisper 2-second input: 194/447 (43.40%).
- Temporary endpoints awaiting readiness: `transmeet-stt-compare-qwen-1028-092130`, `transmeet-stt-compare-nemotron-1028-092130`. Remove both after comparison; the deployment record is in the temporary comparison directory.
- Nemotron live results: full-clip CER 44/447 (9.84%), median 1.923 s; 2-second CER 106/447 (23.71%). Auto Korean language-tag parsing passed. Its endpoint, config, and model were removed after measurement.
- Additional control: explicitly forcing Korean on the existing Whisper endpoint yielded 40.04% CER for 2-second clips; 6-second clips yielded 15.44%.
- Qwen default `ml.g6.xlarge` stayed in Creating for over 30 minutes without container logs. A supported `ml.g6.2xlarge` alternative was requested as `transmeet-stt-compare-qwen-g6-2x-1028-095439`; use the first healthy endpoint and remove both afterward.
- Production frontend build passed, and all 22 frontend unit tests passed.

## Added task 4: Persistent zero-capacity deployment

- [x] Add an Inference Component deployment path: endpoint managed scaling min 0/max 1 with `IDLE_RELEASE`; bootstrap endpoint and component with 1, then request 0 copies.
- [ ] Use only model-supported instance types. Qwen may use prioritized `ml.g6.xlarge` / `ml.g6.2xlarge` pools.
- [ ] Register component autoscaling min 0/max 1, target tracking, and first-failure scale-out alarm.
- [ ] Verify migration of an existing model-based endpoint with the service; do not assume generic UpdateEndpoint establishes support.
- [ ] Recreate retained Nemotron service, preserve remaining Qwen resources, and record actual deployment names.

## Added task 5: Preparation, status, and idle management

**Files:** `backend/app/stt_capacity.py`, `backend/app/{stt,config,aws_clients,ws_handler,main}.py`, `backend/tests/test_stt_capacity.py`.

- [x] Test sleeping/starting/ready/cooling/error classification against desired/current copies and actual endpoint instance count.
- [x] Prepare through `UpdateInferenceComponentRuntimeConfig(CopyCount=1)` with serialized duplicate requests.
- [x] Send authenticated `prepareStt` / `sttStatus` responses over the existing WebSocket.
- [x] Track active recording sessions; release idle unused components after a configurable grace period, alongside native autoscaling.
- [x] Pass `InferenceComponentName` during inference and reject recording until managed capacity is ready.

## Added task 6: Usable settings and recording states

**Files:** `src/lib/{sttProviders,websocket}.ts`, `src/hooks/useWebSocket.ts`, `src/components/{SettingsPanel,ControlPanel}.tsx`, `src/app/page.tsx`.

- [x] Display managed engine preparation/readiness, with a preparation/retry action.
- [x] Poll status without waking a saved selection just because the page opens.
- [x] Selecting an engine can prepare it; microphone starts only after readiness, never during cold start.
- [x] Stop/drain current capture safely when changing to an unready engine.
- [x] Exercise idle → prepare → ready in a real browser with controlled service responses.

## Added task 7: Deploy and verify retained services

- [ ] Extend only the configured endpoint/component IAM permissions and deploy backend environment.
- [ ] Deploy backend and frontend changes; verify the live settings controls.
- [ ] Verify at least one zero → ready → zero cycle with the actual SageMaker component and instance counts.
- [ ] Complete the model comparison where regional capacity permits; report failures and latency conditions honestly.
- [ ] Update operating documentation for retention and automatic idle scaling.
