# Whisper streaming meeting subtitles

The user requested the same SageMaker streaming integration already used for
Qwen and Nemotron. Preserve Whisper large-v3-turbo and its verified JumpStart
weights; the OpenAI hosted API is not part of this change.

## Behavior

Selecting Whisper on a capable backend opens a persistent recording session.
The browser sends 16 kHz mono PCM16 every 160 ms, including silence. Full,
replaceable partial hypotheses appear during speech. Only final utterances
are translated and saved. Stop and stream rotation flush the final short tail.
An older backend or disabled streaming setting retains the existing batch
capture and request path. Korean, English, and automatic language selection
remain available.

Whisper streaming requires the current client to send `sttStreaming: true`
when starting a recording. Missing, false or malformed opt-in retains the
batch path even on a streaming-enabled backend, so a cached older frontend
can continue sending its six-second WAV windows.

## Implementation decisions

- Use the deployed JumpStart artifact in us-east-1:
  `s3://jumpstart-cache-prod-us-east-1/huggingface-asr/huggingface-asr-whisper-large-v3-turbo/artifacts/inference-prepack/v1.0.0/`.
  Do not substitute another Whisper size or download weights from Hugging Face.
- Add `inference/whisper_streaming/`, using pinned PyTorch/Transformers and
  the existing checkpoint format. Load shared weights once and serialize GPU
  inference; audio buffers and hypotheses belong to each session.
- Whisper's adapter re-decodes the current bounded utterance at 1000 or
  2000 ms intervals. It is not a native cache-aware streaming ASR model.
  Coalesce audio received during inference instead of building a backlog of
  stale partial decodes. Never discard accepted audio when coalescing.
- Real GPU smoke demonstrated that fresh room-tone segments can hallucinate
  acknowledgements. The checkpoint's tested BOS no-speech values were near
  zero even for silence, so use pinned Silero VAD 6.2.3 / ONNX Runtime 1.23.2
  on CPU before decoding. Reset state per cumulative waveform and accept speech
  if any raw-audio frame reaches probability 0.5. Verify the bundled model
  SHA-256. Retain all input audio; do not amplify it, use phrase blacklists, or
  impose a minimum speech duration that would remove short acknowledgements.
  Whisper language detection and generation reuse the encoder output.
- Use the existing Qwen endpointing behavior initially: retain nonzero audio,
  preserve pre-roll and final tails, finalize after 800 ms of relative silence
  or 15 seconds. Bound queued audio, total utterance length, sessions and
  outgoing events; surface overload and cancellation explicitly.
- Expose the existing SageMaker `/invocations-bidirectional-stream` contract
  on port 8080, with the required image label and `/ping` health check.
  Reuse `ready/partial/final/ended/error`, stable utterance identifiers, and
  `start_utterance(language, chunk_ms, on_partial)` handles with `feed`,
  asynchronous `finish`, and asynchronous `cancel`.
- Also expose a bounded `/invocations` batch route compatible with the
  existing Whisper `audio/wav` and JSON `audio_input` hex WAV requests,
  `language` (`korean`/`english`), and `task="transcribe"`. Preserve the
  `{"text": [...], "language": ...}` response shape. Batch audio may be up to
  30 seconds; streaming utterances remain capped at 15 seconds. Preserve WAV
  downmix/resampling and reject oversized audio rather than silently truncating.
- Test a separately named staging endpoint first. Prefer updating the existing
  `whisper-large` endpoint to the verified dual-protocol container so only one
  Whisper GPU remains warm, existing batch clients keep working, and the old
  endpoint configuration remains available for rollback. Never delete the old
  model/configuration. Delete only temporary resources created by this task
  after successful production validation.
- Keep the current production instance size unless live quotas or measured
  requirements justify a documented alternative. Present measured AWS pricing
  before infrastructure creation. Pin image digest, weights, and dependencies;
  retain network isolation.
  The attempted `ml.g5.2xlarge` update rolled back because AWS reported
  `InsufficientInstanceCapacity`. Use the already benchmarked `ml.g5.xlarge`
  for the retry; its verified rate is $1.408/hour instead of $1.515/hour.
- Add `WHISPER_STREAMING_ENABLED`, `_ENDPOINT`, `_REGION`, and `_CHUNK_MS`.
  Default streaming off in application settings until a verified endpoint is
  configured; production is enabled only after validation.
- Extend the shared transport and capability-driven UI without changing other
  providers' behavior. Whisper partials follow the same final-only translation
  rule as Qwen/Nemotron. A legacy Whisper endpoint must not require new capacity
  management permissions or readiness calls.
- Preserve existing uncommitted Qwen/Nemotron and unrelated work. Work on the
  new `codex/whisper-streaming` branch with a before snapshot; do not commit
  unrelated changes. Deployment is scoped to Whisper and the required shared
  backend/frontend changes. Compare the current live ECS revision before
  updating it and preserve unrelated live settings and code.

These are implementation defaults selected by the agent under the user's
request to apply the existing streaming architecture to Whisper.

## Acceptance

1. Real-time-paced Korean and English audio yields useful partials before
   recording ends, and correct final transcripts after a short final tail.
2. Automatic and fixed language choices work without Unicode corruption.
3. Silence, continuous speech, malformed PCM, concurrent sessions, slow
   inference, cancellation and disconnect have bounded behavior.
4. Unique finals are translated/saved once; an old stream cannot clear a new
   stream's partial. Disabled/older backends retain batch behavior.
5. Existing backend/frontend tests pass. Container import/health checks and
   real SageMaker bidirectional and batch invocations pass.
6. Benchmarks preserve input hashes, model artifact/image identity, real-time
   pacing, first partial, final lag, CER/WER and errors for 1/2-second intervals.
7. Production backend/frontend advertise Whisper streaming and a real
   application-path recording succeeds. Preserve rollback configuration and
   record final resource state and ongoing cost.

## Evidence used

- Live `DescribeEndpoint`, `DescribeEndpointConfig`, `DescribeModel` and
  `DescribeHubContent` on 2026-10-28; original and latest JumpStart inference
  sources saved in `/tmp/transmeet-whisper-jumpstart-check/`.
- AWS SageMaker custom container contract:
  `https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-inference-code.html`.
- Existing `inference/qwen_streaming/`, `inference/nemotron_streaming/`,
  backend transport, and `docs/stt-models.md`.
