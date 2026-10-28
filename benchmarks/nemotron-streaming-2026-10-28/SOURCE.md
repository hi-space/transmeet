# Nemotron streaming validation

This experiment uses the existing FLEURS Korean and English test recordings in
`../stt-ko-en-2026-10-28/`. Dataset contributors publish `google/fleurs` under
CC BY 4.0; the original attribution, row IDs, conversion details, references,
and WAV SHA-256 hashes are preserved in that folder's `SOURCE.md` and manifests.

## Conditions

- Model: `nvidia/nemotron-3.5-asr-streaming-0.6b`.
- Model files and Transformers: the pinned SageMaker JumpStart 1.0.2 artifact.
- Serving: the custom cache-aware server in `inference/nemotron_streaming/`.
- Compare model lookahead configurations of 320 ms and 560 ms.
- Input: mono 16 kHz PCM16, paced in real time as 160 ms capture frames.
- Each stream waits for `ready` before the simulated capture clock starts.
- Startup time is recorded separately. First-partial latency is measured from
  simulated capture start and includes leading silence in the recording.
- Final lag is measured relative to the end of the input file. It is not the
  same as latency from the speaker's last word.
- Timings include the benchmark host's connection to SageMaker. They do not
  include a user's microphone/device, browser-to-backend network, translation,
  or meeting persistence.
- CER and WER use finalized utterances only. Partial hypotheses are never scored
  as final text. Numbers and spoken number words are not rewritten.
- Interrupted or failed conditions retain their raw events and errors and do
  not receive a rankable corpus accuracy score.

The command is `backend/scripts/benchmark_nemotron_streaming.py`. Reports are
saved after every clip, including failures. Deployment preparation and failed
startup probes are kept separately from the measured, ready-endpoint runs.

## Scope

Twenty read-speech recordings provide a reproducible smoke benchmark, not an
estimate of performance for every meeting. Overlapping speakers, noisy rooms,
and arbitrary multilingual conversations are not represented. Separate tests
cover concurrent streams, input silence, interruption, and the application's
partial/final subtitle lifecycle.
