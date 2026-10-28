# Sources and evaluation scope

Evaluation date: 2026-10-28. Transcribe and Whisper were measured in
`us-east-1`; Qwen and batch Nemotron were measured in `ap-northeast-2`
after the user selected Seoul to replace unsuccessful US East startup attempts.

## Evaluation audio

Audio and reference text are from **FLEURS dataset contributors**, `google/fleurs`,
licensed under **Creative Commons Attribution 4.0 International (CC BY 4.0)**.

- Dataset: <https://huggingface.co/datasets/google/fleurs>
- Dataset card: <https://huggingface.co/datasets/google/fleurs/blob/main/README.md>
- License: <https://creativecommons.org/licenses/by/4.0/>
- Korean: `ko_kr`, test rows 0–9. The exact PCM WAV files from
  `../stt-comparison-2026-10-28/` were copied after verifying their SHA-256 hashes.
- English: `en_us`, test rows 0–9, obtained from
  `refs/convert/parquet/en_us/test/0000.parquet`. Published float WAV audio was
  converted with FFmpeg to 16 kHz, mono, signed 16-bit PCM, without trimming,
  denoising, augmentation, or time stretching.

`manifest-ko.json` and `manifest-en.json` preserve references, dataset IDs,
row positions, durations, and output WAV hashes. The English manifest also
preserves source audio hashes. References are the published human-written
dataset text; they were not independently relabeled for this evaluation.

These are small consecutive samples of read speech, not a random or
representative sample of meetings. The two languages contain different
recordings and are evaluated separately.

## Model and platform documentation

Official model cards, checked on the evaluation date:

- Qwen3-ASR 1.7B:
  <https://huggingface.co/Qwen/Qwen3-ASR-1.7B>
- NVIDIA Nemotron 3.5 ASR 0.6B:
  <https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b>
- OpenAI Whisper large-v3-turbo:
  <https://huggingface.co/openai/whisper-large-v3-turbo>
- Amazon Transcribe partial results:
  <https://docs.aws.amazon.com/transcribe/latest/dg/streaming-partial-results.html>
- Amazon Transcribe streaming language identification:
  <https://docs.aws.amazon.com/transcribe/latest/dg/lang-id-stream.html>
- Amazon Transcribe pricing:
  <https://aws.amazon.com/transcribe/pricing/>
- SageMaker pricing:
  <https://aws.amazon.com/sagemaker/ai/pricing/>
- SageMaker scaling from zero:
  <https://docs.aws.amazon.com/sagemaker/latest/dg/endpoint-auto-scaling-zero-instances.html>
- SageMaker instance pools:
  <https://docs.aws.amazon.com/sagemaker/latest/dg/realtime-endpoints-heterogeneous.html>

Manufacturer benchmark scores are not substituted for this experiment's
results. They use different datasets, normalization, hardware, or streaming
configurations. In particular, the models' native streaming capabilities are
different from the request/response adapters currently used by this app.

Instance and Transcribe rates were retrieved with the AWS Pricing API
`GetProducts`. `pricing.json` preserves the returned product and price
dimensions for US East; `regional-pricing.json` preserves the Seoul query.
Hosting-hour prices and audio-hour prices have different
denominators and must not be treated as directly equivalent unit costs.

## Measurement conditions

- Each provider receives the same WAV bytes for a given sample.
- Whole clips and independent 6-second chunks are measured separately.
- Transcribe receives the whole recording through one streaming session; it is
  not restarted at 6-second boundaries.
- Known-language settings are supplied to the current app adapter: Korean or
  English. Qwen's current adapter still uses automatic language detection.
- Every condition is measured once. Requests to one provider are sequential;
  different providers can run concurrently on their separate serving resources.
- Timings start when an already recorded clip is submitted and end when its
  final text is returned. Transcribe input is sent without real-time pacing.
- Timings do **not** measure first partial captions, live microphone latency,
  browser buffering, the server flush timer, translation, or engine startup.
- Six-second timings aggregate all sequential chunk calls for one original
  recording. They are not the latency of one six-second chunk.
- The app's silence and hallucination filters are not applied.
- Failed preparations or invocations are reported separately. An incomplete
  condition must not be ranked using only successful samples.

`provenance.json` records the source-code hashes and actual endpoint
configuration at the initial run. `regional-deployment.json` records the
successful Seoul deployment and measurement provenance. `readiness.json` and operational probe files record preparation
attempts separately from measured inference. Source files were already
modified in the workspace before this task; the hashes identify the exact
versions used rather than implying the Git commit alone reproduces them.

## Scoring

CER uses the existing `backend/scripts/benchmark_stt.py`: NFKC normalization,
case folding, and removal of non-alphanumeric characters, followed by
Levenshtein distance. Corpus CER is total character edits divided by total
reference characters.

English WER uses NFKC normalization and case folding, removes straight and
curly apostrophes, then extracts Unicode alphanumeric word tokens. Other
punctuation, including hyphens and colons, separates tokens. Corpus WER is
total word edits divided by total reference word tokens.

Neither metric rewrites numerals, spoken number words, abbreviations, dialect
spellings, or synonyms to equivalent forms. Consequently some counted edits
are formatting differences rather than losses of meaning. CER and WER are
different metrics; Korean CER and English WER should not be compared directly.
