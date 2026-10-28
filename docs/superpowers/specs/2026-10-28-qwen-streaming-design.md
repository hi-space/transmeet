# Qwen streaming meeting subtitles

The user asked to use Qwen for real-time meeting subtitles. Korean and English
are the primary languages. Seoul is preferred, Oregon is acceptable, and one
Qwen GPU should stay warm after deployment.

## Behavior

Selecting Qwen opens a persistent recording session. The browser sends 16 kHz
mono PCM16 audio every 160 ms, including silence. The server keeps independent
Qwen streaming state for each utterance and emits full, revisable partial
hypotheses. Partial text appears immediately but is neither translated nor
saved. Each final utterance is translated and saved exactly once. Stop,
disconnect, language changes, and engine changes must flush or explicitly
report a failed tail.

Korean, English, and automatic language selection remain available. Batch
window controls are hidden when the server advertises Qwen streaming.
Older backends without that capability continue to use the existing batch
behavior.

## Implementation decisions

- Use the official `Qwen3ASRModel.init_streaming_state`,
  `streaming_transcribe`, and `finish_streaming_transcribe` API with vLLM.
  This implementation reuses the utterance's accumulated audio and previous
  token hypothesis; it is not a cache-aware RNNT decoder.
- Start with 1-second model chunks; evaluate 1 and 2 seconds using real-time
  audio pacing. Network frames remain 160 ms. Choose the deployed model chunk
  from observed Korean/English latency and accuracy.
- End an utterance after 800 ms silence, or after 15 seconds of continuous
  speech to bound cumulative inference/memory. Preserve pre-roll and short
  final tails. These are implementation defaults, not user-selected settings.
- Use the existing SageMaker bidirectional HTTP/2 transport and
  `ready/partial/final/ended/error` event contract. Parameterize the existing
  transport and subtitle integration only where provider identity differs;
  preserve Nemotron behavior and tests.
- Use a custom, single-model SageMaker endpoint in Seoul. The installed
  bidirectional SDK does not select inference components. Keep the current
  batch endpoint available until the new endpoint and application pass
  verification. After cutover, scale its old copy and host to zero so only
  one Qwen GPU remains warm. Preserve old configuration for explicit rollback.
- Prefer `ml.g6.2xlarge` (same GPU envelope as the current Qwen endpoint).
  Current same-day AWS Pricing evidence is $1.5026/hour in Seoul. The second
  GPU exists only during validation and cutover.
- Pin Qwen weights to the verified regional JumpStart S3 artifact and pin
  inference dependencies/image digest. Retain network isolation. Resolve
  GPU-driver compatibility before creating the endpoint.
- Per-session audio, queue, state, and cancellation are isolated. Serialize
  calls into a shared synchronous vLLM engine. Bound concurrency/queues and
  surface overload instead of silently dropping audio or substituting models.
- Deployment changes only Qwen-related code/configuration and the common
  streaming adaptations required for it. Preserve concurrent meeting-page
  and Nemotron work; compare the live ECS revision before switching.

## Wire contract

Start: `{"type":"start","language":"ko-KR","sample_rate":16000,"chunk_ms":1000}`.
Language is `ko-KR`, `en-US`, or `auto`; model chunk is 1000 or 2000 ms.
Binary frames contain PCM16 little-endian. `{"type":"flush"}` ends the current
utterance; `{"type":"end"}` also closes the stream.

Responses reuse the existing contract:
`{"type":"partial","utterance_id":1,"text":"회의를","language":"Korean"}` and
`{"type":"final","utterance_id":1,"text":"회의를 시작합니다.","language":"Korean"}`.
Each partial replaces the previous hypothesis for its utterance. Empty finals
clear a partial without creating an empty meeting message.

## Acceptance

- Live Korean and English audio produces partials before the recording ends.
- Final audio survives stop; finals are deduplicated and speculative text is
  not saved or translated.
- Silence, malformed audio, two simultaneous sessions, slow inference, and
  transport failure have bounded, tested behavior.
- Existing Transcribe, Whisper, and Nemotron tests pass.
- Real-time benchmark records first partial, lag after speech ends, CER/WER,
  errors, model chunk size, endpoint/region, and exact input hashes.
- Deployed backend/frontend advertise Qwen streaming, recording works through
  the actual application path, and one Qwen GPU remains warm.

## Sources inspected

- Official Qwen repository: `qwen_asr/inference/qwen3_asr.py` and
  `examples/example_qwen3_asr_vllm_streaming.py`, fetched 2026-10-28.
- Official Qwen package metadata: `qwen-asr` 0.0.6, vLLM extra 0.14.0.
- AWS custom inference container contract, bidirectional WebSocket endpoint
  `/invocations-bidirectional-stream`, port 8080.
- Live regional model, endpoint and ECS task metadata saved in
  `/tmp/transmeet-qwen-model-before.json` and
  `/tmp/transmeet-qwen-stream-before-aws.json`.
