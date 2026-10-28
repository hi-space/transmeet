#!/usr/bin/env python3
"""Compare STT engines on identical, already recorded 16 kHz PCM WAV clips.

Manifest: {"samples": [{"id": "ko-1", "audio": "ko-1.wav",
                       "language": "ko", "reference": "사람이 확인한 정답"}]}
Audio paths are relative to the manifest. No audio is downloaded automatically.
These timings measure completed-clip inference, not live subtitle latency.
"""
import argparse
import asyncio
import io
import json
from pathlib import Path
import statistics
import sys
import time
import unicodedata
import wave
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import config
from app.stt import PROVIDERS, STTResult, transcribe_audio


def normalize_transcript(text: str) -> str:
    """Ignore case, whitespace and punctuation; retain spoken lexical differences."""
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


def score_transcript(reference: str, hypothesis: str) -> dict:
    reference = normalize_transcript(reference)
    hypothesis = normalize_transcript(hypothesis)
    # Two-row Levenshtein distance: no extra dependency or quadratic memory.
    previous = list(range(len(hypothesis) + 1))
    for i, expected in enumerate(reference, 1):
        current = [i]
        for j, actual in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (expected != actual)))
        previous = current
    errors = previous[-1]
    return {
        "errors": errors,
        "reference_chars": len(reference),
        "cer": errors / len(reference) if reference else None,
    }


def summarize(rows: list[dict]) -> dict:
    summary = {}
    for provider in dict.fromkeys(row["provider"] for row in rows):
        relevant = [row for row in rows if row["provider"] == provider]
        successful = [row for row in relevant if "error" not in row]
        failed = len(relevant) - len(successful)
        chars = sum(row["reference_chars"] for row in successful)
        errors = sum(row["errors"] for row in successful)
        seconds = sum(row["audio_seconds"] for row in successful)
        elapsed = [row["elapsed_seconds"] for row in successful]
        summary[provider] = {
            "successful_samples": len(successful),
            "failed_samples": failed,
            # Never rank a model that failed some clips as the winner.
            "cer": errors / chars if chars and not failed else None,
            "errors": errors,
            "reference_chars": chars,
            "median_seconds": statistics.median(elapsed) if elapsed else None,
            "real_time_factor": sum(elapsed) / seconds if seconds else None,
        }
    return summary


def read_pcm(audio: bytes) -> bytes:
    with wave.open(io.BytesIO(audio), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (16000, 1, 2, "NONE"):
            raise ValueError("Audio must be uncompressed 16 kHz, mono, 16-bit PCM WAV.")
        return wav.readframes(wav.getnframes())


def split_audio(audio: bytes, chunk_seconds: float) -> list[bytes]:
    if chunk_seconds <= 0:
        return [audio]
    pcm = read_pcm(audio)
    step = max(2, int(chunk_seconds * 16000) * 2)
    chunks = []
    for start in range(0, len(pcm), step):
        out = io.BytesIO()
        with wave.open(out, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm[start:start + step])
        chunks.append(out.getvalue())
    return chunks


async def transcribe_clip(audio: bytes, language: str) -> STTResult:
    from amazon_transcribe.client import TranscribeStreamingClient
    from amazon_transcribe.model import TranscriptEvent

    pcm = read_pcm(audio)
    options = {
        "media_sample_rate_hz": 16000, "media_encoding": "pcm",
        "language_code": {"ko": "ko-KR", "en": "en-US"}.get(language),
    }
    if language == "auto":
        options.update(identify_multiple_languages=True, language_options=["ko-KR", "en-US"])
    client = TranscribeStreamingClient(region=config.REGION)
    stream = await client.start_stream_transcription(**options)
    final_text = []
    detected_language = language if language != "auto" else ""

    async def send():
        # Feed an existing clip without wall-clock pacing, using 100ms chunks.
        for start in range(0, len(pcm), 3200):
            await stream.input_stream.send_audio_event(audio_chunk=pcm[start:start + 3200])
        await stream.input_stream.end_stream()

    async def collect():
        nonlocal detected_language
        seen = set()
        async for event in stream.output_stream:
            if not isinstance(event, TranscriptEvent):
                continue
            for result in event.transcript.results:
                if result.is_partial or not result.alternatives:
                    continue
                if result.result_id in seen:
                    continue
                seen.add(result.result_id)
                final_text.append(result.alternatives[0].transcript)
                detected_language = getattr(result, "language_code", None) or detected_language

    tasks = [asyncio.create_task(send()), asyncio.create_task(collect())]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return STTResult(" ".join(final_text).strip(), detected_language)


async def recognize(provider: str, audio: bytes, language: str) -> STTResult:
    if provider == "transcribe":
        return await transcribe_clip(audio, language)
    return await transcribe_audio(provider, audio, language)


async def measure(provider: str, audio: bytes, language: str, reference: str,
                  audio_seconds: float, timeout: float, chunk_seconds: float = 0) -> dict:
    row = {"provider": provider, "audio_seconds": audio_seconds, "reference": reference}
    started = time.perf_counter()
    try:
        async def run():
            # Transcribe keeps one session over the whole clip, as in the app.
            chunks = [audio] if provider == "transcribe" else split_audio(audio, chunk_seconds)
            results = [await recognize(provider, chunk, language) for chunk in chunks]
            return STTResult(" ".join(r.text for r in results if r.text),
                             results[-1].language if results else "")

        result = await asyncio.wait_for(run(), timeout=timeout)
        row.update(text=result.text, language=result.language,
                   **score_transcript(reference, result.text))
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["elapsed_seconds"] = round(time.perf_counter() - started, 4)
    return row


async def run_benchmark(args) -> dict:
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text())
    samples = manifest["samples"]
    if not samples:
        raise ValueError("The manifest must contain at least one sample.")
    # Validate every sample before making any billed inference request.
    prepared = []
    for sample in samples:
        if not isinstance(sample.get("reference"), str):
            raise ValueError("Every sample requires a human-verified reference string.")
        language = sample.get("language", "ko")
        if language not in ("ko", "en", "auto"):
            raise ValueError(f"Unsupported sample language: {language}")
        audio = (manifest_path.parent / sample["audio"]).read_bytes()
        seconds = len(read_pcm(audio)) / 32000
        if not seconds:
            raise ValueError(f"Empty audio: {sample['audio']}")
        prepared.append((sample, audio, language, seconds))
    rows = []
    for repeat in range(args.repeat):
        for sample, audio, language, seconds in prepared:
            for provider in args.providers:
                row = await measure(provider, audio, language, sample["reference"],
                                    seconds, args.timeout, args.chunk_seconds)
                row.update(sample_id=sample["id"], repeat=repeat + 1)
                rows.append(row)
                status = row.get("error", f"CER={row['cer']:.3f}" if row.get("cer") is not None else "unscored")
                print(f"{sample['id']} / {provider}: {status}", file=sys.stderr, flush=True)
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "region": config.REGION,
        "source": manifest.get("source"),
        "license": manifest.get("license"),
        "conditions": {
            "input": "identical pre-recorded 16 kHz mono PCM clips",
            "timing": "completed-clip wall time; Transcribe sent without real-time pacing",
            "normalization": "NFKC, casefold, alphanumeric characters; no number/abbreviation rewriting",
            "sagemaker_chunk_seconds": args.chunk_seconds,
            "repetitions": args.repeat,
        },
        "summary": summarize(rows),
        "results": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--providers", nargs="+", choices=PROVIDERS, default=list(PROVIDERS))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--chunk-seconds", type=float, default=0,
                        help="Split SageMaker input into chunks; 6 approximates the default app input, 2 reproduces the previous baseline, 0 uses whole clips.")
    args = parser.parse_args()
    if args.repeat < 1 or args.timeout <= 0 or args.chunk_seconds < 0:
        parser.error("repeat/timeout must be positive and chunk-seconds nonnegative")
    report = asyncio.run(run_benchmark(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return int(any(item["failed_samples"] for item in report["summary"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
