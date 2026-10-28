# Nemotron streaming implementation plan

> **For agentic workers:** Implement the approved streaming design with test-driven development. Independent frontend and backend transport tasks may run in parallel after reading the wire contract below. Preserve all pre-existing workspace changes.

**Goal:** Show Nemotron partial transcripts while the user speaks, then translate and save finalized utterances without waiting for a six-second audio window.

**Architecture:** A custom SageMaker container keeps a separate streaming decoder and feature cache for each recording. The FastAPI backend connects through SageMaker's bidirectional HTTP/2 API, feeds short PCM chunks, and maps partial/final events into the existing subtitle and persistence pipeline. Browser capture sends Nemotron audio every 160 ms.

**Tech Stack:** Existing Next.js/FastAPI app, pinned `aws-sdk-sagemaker-runtime-http2==0.12.0`, PyTorch/Transformers from the deployed Nemotron JumpStart 1.0.2 artifact, SageMaker custom hosting container.

**Spec:** User approved the preceding proposal: continuous input, preserved model state, intermediate original-language subtitles, final utterance translation/storage, and Korean/English latency/accuracy validation.

## Global constraints

- Keep the model `nvidia/nemotron-3.5-asr-streaming-0.6b` and JumpStart artifact version `1.0.2`.
- Preserve Whisper, Transcribe, Qwen, language controls, recording downloads, and all pre-existing edits.
- Default model chunk size is 560 ms; benchmark 320 ms and 560 ms. Browser transport interval is independently 160 ms.
- Keep audio bounded; handle stop, disconnect, malformed input, backpressure, and long recordings explicitly.
- Never save partial transcripts as final utterances. Final events are unique per utterance.
- Existing `NEMOTRON_ASR_ENDPOINT` remains the batch endpoint. Add a separately named streaming endpoint and an explicit enable setting for rollout.
- The installed bidirectional SDK has no inference-component selector. Use a single-model streaming endpoint and retain existing batch resources.
- Maintain one warm streaming instance. Do not delete existing AWS resources.
- Work in the current workspace because the running feature depends on its existing uncommitted language/capacity changes. Record a before snapshot in temporary storage; do not commit unrelated work.

## Wire contract

The backend sends one complete UTF-8 JSON start message:

```json
{"type":"start","language":"ko-KR","sample_rate":16000,"chunk_ms":560}
```

`language` is `ko-KR`, `en-US`, or `auto`; `chunk_ms` is 320 or 560. Subsequent complete binary messages contain mono PCM16 little-endian audio at 16 kHz, including silence. A complete UTF-8 `{"type":"end"}` message flushes the final utterance. A complete `{"type":"flush"}` message finalizes the current utterance while keeping the connection open.

The container sends complete JSON messages:

```json
{"type":"ready"}
{"type":"partial","utterance_id":1,"text":"회의를","language":"ko-KR"}
{"type":"final","utterance_id":1,"text":"회의를 시작합니다.","language":"ko-KR"}
{"type":"ended"}
{"type":"error","message":"..."}
```

Partial text is the full current hypothesis, not a delta. Language tags are removed from text and reflected in `language`; `auto` may remain unknown until the model emits a language. An empty final clears the corresponding partial but is not translated or persisted. All event-stream payloads are complete messages (`CompletionState=COMPLETE`); do not confuse a partial transcript with a fragmented transport message.

## Review focus

1. Stop or language/engine switching immediately after a short utterance must retain the tail once.
2. Two simultaneous recordings must not share audio, decoder cache, or hypotheses.
3. Extended silence and recordings longer than the 30-minute SageMaker connection limit must not leak memory or silently lose audio.
4. A failed/slow model stream must surface an error and release capacity without blocking the WebSocket loop forever.
5. AUTO language and same-language translation must continue using actual recognition results, not the requested language alone.

## Task 1: Streaming inference container

**Files:** `inference/nemotron_streaming/`, `backend/tests/test_nemotron_engine.py`.

**Produces:** The wire contract above, `/ping`, `/invocations-bidirectional-stream`, container build instructions.

- [x] Add failing tests for PCM endpointing, feature chunk continuity, short-tail flush, final deduplication, and independent sessions.
- [x] Implement bounded incremental feature extraction and RNNT generation using the bundled Transformers streaming interface.
- [x] Implement protocol validation, silence-based utterance finalization, end/flush, cancellation, and capacity bounds.
- [x] Build a container with the SageMaker bidirectional capability label and local tests.

## Task 2: Backend streaming transport and subtitle integration

**Files:** `backend/app/nemotron_stream.py`, `backend/app/ws_handler.py`, `backend/app/config.py`, `backend/requirements.txt`, related backend tests.

**Consumes:** Container wire contract. **Produces:** Existing `subtitle_stream` events and final `_process_segment` calls.

- [x] Add failing tests for partial/final flow, final-on-stop, duplicate final handling, failures, and old-session language snapshots.
- [x] Implement credential refresh via the AWS credential provider chain, bidirectional transport, bounded queues, and graceful close.
- [x] Route enabled Nemotron recordings through the continuous stream; leave the batch adapter available for comparison/rollback.
- [x] Verify relevant backend tests and the complete backend suite.

## Task 3: Browser capture and settings

**Files:** `src/lib/sttProviders.ts`, `src/components/SettingsPanel.tsx`, `src/app/page.tsx`, `tests/sttProviders.test.mjs`, relevant browser tests.

**Consumes:** Existing partial/final subtitle messages. **Produces:** 160 ms Nemotron audio messages and accurate settings copy.

- [x] Update tests to require continuous Nemotron capture independently of saved batch-window settings.
- [x] Hide batch-window controls for streaming Nemotron and explain live intermediate transcripts.
- [x] Preserve audio on stop/setting changes and avoid automatically translating speculative Nemotron partials.
- [x] Run settings/UI tests and TypeScript/build verification.

## Task 4: Deploy and measure

**Files:** `backend/scripts/deploy_nemotron_streaming.py`, `backend/scripts/benchmark_nemotron_streaming.py`, `.env.example`, `infra/lib/transmeet-stack.ts`, `docs/stt-models.md`, benchmark artifacts.

- [x] Add a reviewable dry-run deployment command with pinned artifact/image and a separate endpoint name.
- [x] Build/push the container and create the streaming endpoint; verify a real streaming request.
- [x] Compare 320/560 ms settings using the same Korean and English recordings, recording first-partial latency, final latency, and recognition errors.
- [x] Add scoped IAM permission and streaming environment variables; build/version/deploy the backend and frontend.
- [x] Verify running configuration, browser behavior, and CloudWatch errors; record actual rollout and limitations.
- [x] Perform a fresh independent review and fix material findings.

## Evidence and decisions

- Deployed `inference.py` calls `processor(audio)` and `model.generate()` once per complete request; it does not preserve streaming state.
- NVIDIA's model card documents the cache-aware streaming interface and 320/560 ms configurations.
- SageMaker documents the Docker label and WebSocket `/invocations-bidirectional-stream` contract, and a maximum 30-minute bidirectional connection.
- The initial backend/settings baselines were run before edits; results are recorded as they complete.
- Baseline: backend 333 tests passed; frontend settings 29 tests passed.
- Deployment ruling: use `ml.g5.xlarge`. AWS rejected a third `ml.g5.2xlarge` instance (quota 2, usage 2); `ml.g5.xlarge` quota is 4 and EC2 confirms the same A10G GPU memory. Validate its smaller CPU/RAM envelope through real inference.
- Deployment ruling: omit `VolumeSizeInGB`; the live SageMaker API rejects this override for local-NVMe G5 instances.
- Early independent review caught trailing RNNT blanks hiding AUTO language tags and socket cancellation releasing capacity before GPU work finished. Both have regression tests and fixes; 28 container/deployment tests passed afterward.
- Hosting correction: pin `InferenceAmiVersion=al2-ami-sagemaker-inference-gpu-3-1` to match the artifact's CUDA 12.4 PyTorch wheel, and expose NVIDIA compute/utility devices explicitly. The first new endpoint was still `Creating`; SageMaker refused its cancellation/update. The corrected deployment uses `transmeet-nemotron-streaming-v2`. The first new deployment remains unactivated and must be revisited when AWS permits cleanup.
- SDK compatibility ruling: Transcribe 0.6.4 requires CRT 0.26; SageMaker HTTP/2 requires CRT 0.32. Use a separate SDK process/venv with bounded, length-prefixed pipes. This preserves the existing Transcribe runtime.
- Shared workspace contains a concurrent change moving batch-provider region configuration toward Seoul. Preserve it; activate streaming with a selective ECS task revision and scoped IAM policy rather than deploying the entire CDK stack.

- Live validation caught fixed-RMS admission dropping quiet recordings and internal locale tags leaking into captions. Both were reproduced, fixed and reviewed; all 20 input WAVs now reach the model byte-for-byte. The corrected Seoul GPU image is `c20c3f92bf90b7786e4a5122e4404e682c418fca8f2d7442601595a7261e6c23`. Full merged backend verification: 473 passed.

- Completed: corrected Seoul endpoint InService (one GPU), all 40 benchmark requests successful, AUTO/concurrent/35-second-silence checks passed. Latest live backend image was preserved and only four Nemotron settings changed; task definition revision 35 stabilized at one running / zero pending. Live-role ASR and Bedrock translation probe passed, frontend HTTP200 and deployed UI verified, temporary validation audio and all failed US streaming endpoints removed.
- Authorization verification: the live HTTP/2 runtime returned403 with only the bidirectional action. Adding standard `sagemaker:InvokeEndpoint` on the same single endpoint ARN made the identical ECS probe succeed. Source IAM includes both actions and DescribeEndpoint.
