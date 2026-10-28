#!/usr/bin/env python3
"""Measure live Nemotron captions on verified, locally stored PCM WAV clips.

Example:
  python backend/scripts/benchmark_nemotron_streaming.py \
    --manifest benchmarks/stt-ko-en-2026-10-28/manifest-ko.json \
    --endpoint transmeet-nemotron-streaming --chunk-ms 560 --output report.json

No audio is downloaded. Each 160 ms frame is sent only after its simulated
capture ends. Startup is measured separately; accuracy uses finalized text.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import config
from app.nemotron_stream import NemotronStream
from scripts.benchmark_stt import normalize_transcript, read_pcm, score_transcript


MODEL_ID = "nvidia/nemotron-3.5-asr-streaming-0.6b"
ARTIFACT_VERSION = "1.0.2"
PCM_BYTES_PER_SECOND = 32000
FRAME_BYTES = 5120


def score_words(reference: str, hypothesis: str) -> dict:
    """SOURCE.md's English WER: Unicode words, without number rewriting."""
    def words(text):
        text = unicodedata.normalize("NFKC", text).casefold()
        text = text.replace("'", "").replace("’", "")
        return "".join(char if char.isalnum() else " " for char in text).split()

    reference, hypothesis = words(reference), words(hypothesis)
    previous = list(range(len(hypothesis) + 1))
    for i, expected in enumerate(reference, 1):
        current = [i]
        for j, actual in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (expected != actual)))
        previous = current
    errors = previous[-1]
    return {"word_errors": errors, "reference_words": len(reference),
            "wer": errors / len(reference) if reference else None}


def prepare_samples(manifest_path: Path, limit: int | None = None):
    """Verify the whole selected condition before opening a billed stream."""
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("The manifest must contain samples")
    prepared, seen = [], set()
    for sample in samples[:limit]:
        sample_id = sample.get("id")
        if not isinstance(sample_id, str) or not sample_id or sample_id in seen:
            raise ValueError("Every sample requires a unique nonempty id")
        seen.add(sample_id)
        if sample.get("language") not in ("ko", "en"):
            raise ValueError(f"{sample_id}: language must be ko or en")
        reference = sample.get("reference")
        if not isinstance(reference, str) or not normalize_transcript(reference):
            raise ValueError(f"{sample_id}: a nonempty reference is required")
        audio = (manifest_path.parent / sample["audio"]).read_bytes()
        digest = hashlib.sha256(audio).hexdigest()
        expected = sample.get("sha256")
        if not isinstance(expected, str) or digest != expected.lower():
            raise ValueError(f"{sample_id}: audio SHA-256 does not match the manifest")
        pcm = read_pcm(audio)
        seconds = len(pcm) / PCM_BYTES_PER_SECOND
        if not seconds or len(pcm) % 2:
            raise ValueError(f"{sample_id}: empty or incomplete PCM audio")
        if "audio_seconds" in sample and not math.isclose(
            seconds, sample["audio_seconds"], rel_tol=0, abs_tol=1 / 16000,
        ):
            raise ValueError(f"{sample_id}: audio duration does not match the manifest")
        prepared.append(({**sample, "audio_seconds": seconds, "sha256": digest,
                          "sha256_verified": True}, pcm))
    return manifest, hashlib.sha256(manifest_bytes).hexdigest(), prepared


async def measure_clip(
    sample: dict, pcm: bytes, *, endpoint: str, chunk_ms: int = 560,
    region: str = "us-east-1", auto: bool = False, ready_timeout: float = 45,
    close_timeout: float = 15, stream_factory=None, clock=time.perf_counter,
    sleep=asyncio.sleep,
) -> dict:
    """Observe one transport; inject only the clock/pacer and external stream."""
    started = clock()
    audio_seconds = len(pcm) / PCM_BYTES_PER_SECOND
    language = "auto" if auto else {"ko": "ko-KR", "en": "en-US"}[sample["language"]]
    ready_at = audio_start = first_partial = last_final = None
    startup_done = asyncio.Event()
    events, final_texts, error_messages, detected_languages = [], [], [], []
    seen_finals = set()
    partial_count = 0
    ended = False
    stream = None
    send_seconds = None

    def fail(error):
        message = str(error) or type(error).__name__
        if message not in error_messages:
            error_messages.append(message)

    async def on_event(event):
        nonlocal ready_at, first_partial, last_final, partial_count, ended
        now = clock()
        # Only the transcript protocol enters reports, never SDK/credential objects.
        raw = {key: event[key] for key in (
            "type", "stream_id", "utterance_id", "text", "language", "message",
        ) if key in event}
        events.append({
            **raw, "elapsed_seconds": round(now - started, 6),
            "audio_elapsed_seconds": round(now - audio_start, 6) if audio_start is not None else None,
        })
        kind = event["type"]
        if kind == "ready":
            ended = False
            if ready_at is None:
                ready_at = now
                startup_done.set()
        elif kind == "error":
            fail(event.get("message") or "Nemotron stream failed")
            startup_done.set()
        elif kind == "ended":
            ended = True
            if audio_start is None and not error_messages:
                fail("Nemotron stream ended before audio capture started")
                startup_done.set()
        elif kind in ("partial", "final"):
            text = event.get("text", "").strip()
            if text and audio_start is None:
                fail("Nemotron returned a transcript before audio capture started")
                startup_done.set()
                return
            if kind == "partial" and text:
                partial_count += 1
                if first_partial is None:
                    first_partial = now - audio_start
            elif kind == "final":
                identity = (event.get("stream_id"), event.get("utterance_id"))
                if identity in seen_finals:
                    return
                seen_finals.add(identity)
                if text:
                    final_texts.append(text)
                    last_final = now - audio_start
                    detected = event.get("language")
                    if detected and detected not in detected_languages:
                        detected_languages.append(detected)

    try:
        stream = (stream_factory or NemotronStream)(
            endpoint_name=endpoint, language=language, chunk_ms=chunk_ms, region=region,
        )
        stream.start(on_event)
        try:
            await asyncio.wait_for(startup_done.wait(), timeout=ready_timeout)
        except TimeoutError:
            fail(f"Ready timeout after {ready_timeout:g}s")
        if stream.error:
            fail(stream.error)
        if not error_messages:
            audio_start = clock()
            try:
                async with asyncio.timeout(audio_seconds + close_timeout):
                    for offset in range(0, len(pcm), FRAME_BYTES):
                        frame = pcm[offset:offset + FRAME_BYTES]
                        capture_end = (offset + len(frame)) / PCM_BYTES_PER_SECOND
                        await sleep(max(0.0, audio_start + capture_end - clock()))
                        if stream.error:
                            fail(stream.error)
                        if error_messages:
                            break
                        await stream.send_audio(frame)
                    send_seconds = clock() - audio_start
            except TimeoutError:
                fail("Audio send timed out")
    except Exception as exc:
        fail(exc)
    finally:
        if stream is not None:
            try:
                await asyncio.wait_for(stream.close(), timeout=close_timeout)
            except TimeoutError:
                fail(f"Close timeout after {close_timeout:g}s")
            except Exception as exc:
                fail(exc)
            if stream.error:
                fail(stream.error)
    completed = clock()
    if not error_messages and not ended:
        fail("Nemotron stream closed without an ended acknowledgement")
    row = {
        "sample_id": sample["id"], "language": sample["language"],
        "requested_language": language, "reference": sample["reference"],
        "audio_seconds": audio_seconds, "text": " ".join(final_texts),
        "detected_languages": detected_languages,
        "startup_seconds": round(ready_at - started, 6) if ready_at is not None else None,
        "first_partial_seconds": round(first_partial, 6) if first_partial is not None else None,
        "final_lag_seconds": round(last_final - audio_seconds, 6) if last_final is not None else None,
        "end_to_end_seconds": round(completed - audio_start, 6) if audio_start is not None else None,
        "audio_send_seconds": round(send_seconds, 6) if send_seconds is not None else None,
        "total_seconds": round(completed - started, 6),
        "partial_count": partial_count, "final_count": len(final_texts),
        "partial_before_audio_end": first_partial is not None and first_partial < audio_seconds,
        "error_count": len(error_messages), "events": events,
    }
    if error_messages:
        row.update(error="; ".join(error_messages), error_messages=error_messages)
    else:
        row.update(score_transcript(sample["reference"], row["text"]))
        if sample["language"] == "en":
            row.update(score_words(sample["reference"], row["text"]))
    return row


def summarize(rows: list[dict], planned_samples: int) -> dict:
    valid = [row for row in rows if not row.get("error_count")]
    failed = len(rows) - len(valid)
    errors = sum(row["errors"] for row in valid)
    chars = sum(row["reference_chars"] for row in valid)
    word_errors = sum(row.get("word_errors", 0) for row in valid)
    words = sum(row.get("reference_words", 0) for row in valid)
    cer, wer = errors / chars if chars else None, word_errors / words if words else None
    eligible = bool(valid) and len(valid) == planned_samples and not failed
    diagnostics = {
        "cer": cer, "errors": errors, "reference_chars": chars,
        "wer": wer, "word_errors": word_errors, "reference_words": words,
        "partial_count": sum(row["partial_count"] for row in valid),
        "partial_before_audio_end_samples": sum(row["partial_before_audio_end"] for row in valid),
        "samples_with_partial": sum(row["first_partial_seconds"] is not None for row in valid),
    }
    for metric in ("startup_seconds", "first_partial_seconds", "final_lag_seconds", "end_to_end_seconds"):
        values = [row[metric] for row in valid if row[metric] is not None]
        diagnostics["median_" + metric] = statistics.median(values) if values else None
    return {
        "successful_samples": len(valid), "failed_samples": failed,
        "pending_samples": planned_samples - len(rows),
        "error_count": sum(row["error_count"] for row in rows),
        "ranking_allowed": eligible, "cer": cer if eligible else None,
        "wer": wer if eligible else None, "valid_only": diagnostics,
    }


def save_report(path: Path, report: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


async def run_benchmark(args, *, stream_factory=None, clock=time.perf_counter, sleep=asyncio.sleep):
    if args.chunk_ms not in (320, 560) or not args.endpoint.strip():
        raise ValueError("A streaming endpoint and chunk-ms of 320 or 560 are required")
    ready_timeout = getattr(args, "ready_timeout", 45)
    close_timeout = getattr(args, "close_timeout", 15)
    if any(not math.isfinite(value) or value <= 0 for value in (ready_timeout, close_timeout)):
        raise ValueError("Timeouts must be finite and positive")
    path = args.manifest.resolve()
    manifest, manifest_hash, prepared = prepare_samples(path, args.limit)
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_id": MODEL_ID, "jumpstart_artifact_version": ARTIFACT_VERSION,
        "endpoint": args.endpoint, "region": args.region,
        "manifest": str(path), "manifest_sha256": manifest_hash,
        "source": manifest.get("source"), "license": manifest.get("license"),
        "planned_samples": len(prepared), "status": "running",
        "conditions": {
            "mode": "realtime_streaming", "model_chunk_ms": args.chunk_ms,
            "capture_frame_ms": 160, "language_mode": "auto" if args.auto else "manifest",
            "input": "SHA-256 verified 16 kHz mono PCM16 WAV; no trimming or augmentation",
            "pacing": "await ready, then send every frame at its audio end time",
            "startup": "stream start to first ready; excluded from audio-relative latency",
            "final_lag": "last unique nonempty final minus audio end; trailing silence can make this negative",
            "end_to_end": "audio capture start through stream close, including audio duration",
            "event_elapsed_origin": "stream start; audio_elapsed_seconds starts after ready",
            "cer_normalization": "benchmark_stt.score_transcript: NFKC, casefold, alphanumeric characters",
            "wer_normalization": "English only: NFKC, casefold, remove straight/curly apostrophes, Unicode alphanumeric words",
            "model_identity": "expected pinned artifact; endpoint deployment provenance is checked separately",
            "ready_timeout_seconds": ready_timeout, "close_timeout_seconds": close_timeout,
        },
        "results": [],
    }
    report["summary"] = summarize([], len(prepared))
    save_report(args.output, report)
    for sample, pcm in prepared:
        row = await measure_clip(
            sample, pcm, endpoint=args.endpoint, chunk_ms=args.chunk_ms, region=args.region,
            auto=args.auto, ready_timeout=ready_timeout, close_timeout=close_timeout,
            stream_factory=stream_factory, clock=clock, sleep=sleep,
        )
        row.update(audio=sample["audio"], sha256=sample["sha256"], sha256_verified=True)
        report["results"].append(row)
        report["summary"] = summarize(report["results"], len(prepared))
        if len(report["results"]) == len(prepared):
            report["status"] = "incomplete" if report["summary"]["failed_samples"] else "complete"
        save_report(args.output, report)
        outcome = row.get("error", f"CER={row.get('cer', 0):.3f}")
        print(f"{sample['id']}: {outcome}", file=sys.stderr, flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--chunk-ms", type=int, choices=(320, 560), default=560)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, help="Use only the first N clips; default: all clips")
    parser.add_argument("--auto", action="store_true", help="Use automatic language detection")
    parser.add_argument("--region", default=config.REGION)
    parser.add_argument("--ready-timeout", type=float, default=45)
    parser.add_argument("--close-timeout", type=float, default=15)
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(run_benchmark(args))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    return int(report["summary"]["failed_samples"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
