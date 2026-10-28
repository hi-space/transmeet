#!/usr/bin/env python3
"""Compare four STT providers using identical, hash-verified real-time capture.

Run one provider and manifest per command. Whisper defaults to its deployed
streaming transport, periodically re-decoding a bounded utterance. Use
--whisper-mode batch for explicit six-second request/response framing.
The caller verifies deployments before execution.
"""
import argparse
import asyncio
from collections import deque
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import statistics
import sys
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import stt
from app.config import config
from app.nemotron_stream import NemotronStream, QwenStream, WhisperStream
from scripts.benchmark_nemotron_streaming import (
    measure_clip, prepare_samples, save_report, summarize,
)

PROVIDERS = ("transcribe", "whisper", "qwen3-asr", "nemotron")
PCM_BYTES_PER_SECOND = 32000


def provider_settings(provider, *, endpoint=None, region=None, whisper_mode="streaming"):
    if whisper_mode not in ("streaming", "batch"):
        raise ValueError("whisper_mode must be streaming or batch")
    whisper_streaming = whisper_mode == "streaming"
    settings = {
        "qwen3-asr": {
            "endpoint": config.QWEN_STREAMING_ENDPOINT or "transmeet-qwen-streaming",
            "region": config.QWEN_STREAMING_REGION or "ap-northeast-2",
            "chunk_ms": 1000, "mode": "native_streaming",
            "transport": "sagemaker_bidirectional_http2",
            "decode_strategy": "qwen_cumulative_audio_and_text_prefix",
            "model_id": "Qwen/Qwen3-ASR-1.7B", "artifact_version": "1.0.0",
        },
        "nemotron": {
            "endpoint": config.NEMOTRON_STREAMING_ENDPOINT or "transmeet-nemotron-streaming",
            "region": config.NEMOTRON_STREAMING_REGION or "ap-northeast-2",
            "chunk_ms": 560, "mode": "native_streaming",
            "transport": "sagemaker_bidirectional_http2", "decode_strategy": "rnnt_streaming",
            "model_id": "nvidia/nemotron-3.5-asr-streaming-0.6b", "artifact_version": "1.0.2",
        },
        "whisper": {
            "endpoint": (
                config.WHISPER_STREAMING_ENDPOINT.strip() or stt.endpoint_for("whisper")
            ) if whisper_streaming else stt.endpoint_for("whisper"),
            "region": (
                config.WHISPER_STREAMING_REGION.strip() or stt.region_for("whisper")
            ) if whisper_streaming else stt.region_for("whisper"),
            "chunk_ms": config.WHISPER_STREAMING_CHUNK_MS if whisper_streaming else 6000,
            "mode": "periodic_utterance_streaming" if whisper_streaming else "serial_batch_windows",
            "transport": "sagemaker_bidirectional_http2" if whisper_streaming else "sagemaker_request_response",
            "decode_strategy": "bounded_utterance_redecode" if whisper_streaming else "independent_six_second_windows",
            "whisper_mode": whisper_mode,
            "model_id": "openai/whisper-large-v3-turbo", "artifact_version": "1.0.0",
        },
        "transcribe": {
            "endpoint": None, "region": config.REGION,
            "chunk_ms": None, "mode": "native_streaming",
            "transport": "amazon_transcribe_streaming", "decode_strategy": "managed_streaming_recognition",
            "model_id": "Amazon Transcribe Streaming (service managed)", "artifact_version": None,
        },
    }
    if provider not in settings:
        raise ValueError(f"provider must be one of {PROVIDERS}")
    selected = dict(settings[provider])
    if provider == "whisper" and whisper_streaming and (
        type(selected["chunk_ms"]) is not int or selected["chunk_ms"] not in (1000, 2000)
    ):
        raise ValueError("Whisper streaming requires a configured 1000 or 2000 ms model interval")
    if provider == "whisper" and (
        (endpoint is not None and endpoint != selected["endpoint"])
        or (region is not None and region != selected["region"])
    ):
        raise ValueError("Whisper must use the endpoint and region declared in app configuration")
    if region is not None:
        selected["region"] = region
    if provider == "transcribe":
        selected["endpoint"] = f"transcribestreaming.{selected['region']}.amazonaws.com"
        if endpoint is not None and endpoint != selected["endpoint"]:
            raise ValueError("Transcribe uses its managed regional endpoint")
    elif endpoint is not None:
        selected["endpoint"] = endpoint
    if not selected["region"].strip() or not selected["endpoint"].strip():
        raise ValueError("A nonempty provider region and endpoint are required")
    return selected


def transcribe_parameters(language):
    if language not in ("ko-KR", "en-US"):
        raise ValueError("This comparison requires known ko-KR or en-US language")
    # Matches ws_handler._run_transcribe_streaming for both known languages.
    return {
        "language_code": language, "media_sample_rate_hz": 16000,
        "media_encoding": "pcm", "show_speaker_label": True,
    }


class TranscribeStream:
    """Native Transcribe SDK adapted to the shared observation protocol."""

    def __init__(self, *, endpoint_name, language, chunk_ms, region, client_factory=None):
        self.language = language
        self.region = region
        self.parameters = transcribe_parameters(language)
        self.client_factory = client_factory
        self.error = None
        self._stream = self._task = None
        self._closing = False
        self._ids = {}
        self._finalized = set()

    def start(self, on_event):
        if self._task is not None:
            raise RuntimeError("Transcribe stream was already started")
        self._handler = on_event
        self._task = asyncio.create_task(self._run())

    async def _fail(self, exc):
        if self.error is None:
            self.error = exc
            await self._handler({"type": "error", "message": str(exc) or type(exc).__name__})

    async def _run(self):
        try:
            factory = self.client_factory
            if factory is None:
                from amazon_transcribe.client import TranscribeStreamingClient
                factory = TranscribeStreamingClient
            client = factory(region=self.region)
            self._stream = await client.start_stream_transcription(**self.parameters)
            await self._handler({"type": "ready", "stream_id": 1})
            iterator = self._stream.output_stream.__aiter__()
            while True:
                try:
                    event = await asyncio.wait_for(iterator.__anext__(), timeout=30)
                except StopAsyncIteration:
                    break
                transcript = getattr(event, "transcript", None)
                for result in getattr(transcript, "results", None) or []:
                    alternatives = result.alternatives or []
                    if not alternatives:
                        continue
                    identity = result.result_id
                    if not identity:
                        raise RuntimeError("Transcribe returned a result without an identifier")
                    if identity in self._finalized:
                        continue
                    number = self._ids.setdefault(identity, len(self._ids) + 1)
                    kind = "partial" if result.is_partial else "final"
                    if kind == "final":
                        self._finalized.add(identity)
                    await self._handler({
                        "type": kind, "stream_id": 1, "utterance_id": number,
                        "text": (alternatives[0].transcript or "").strip(),
                        "language": getattr(result, "language_code", None) or self.language,
                    })
            if not self._closing:
                raise RuntimeError("Transcribe output ended before capture finished")
            await self._handler({"type": "ended", "stream_id": 1})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._fail(exc)

    async def send_audio(self, pcm):
        if self.error:
            raise self.error
        if self._closing or self._stream is None:
            raise RuntimeError("Transcribe is not accepting audio")
        if len(pcm) % 2:
            raise ValueError("PCM16 requires complete samples")
        try:
            if pcm:
                await self._stream.input_stream.send_audio_event(audio_chunk=pcm)
        except Exception as exc:
            await self._fail(exc)
            raise

    async def close(self):
        try:
            if not self._closing:
                self._closing = True
                if self._stream is not None:
                    await self._stream.input_stream.end_stream()
                elif self._task is not None and not self._task.done():
                    self._task.cancel()
                    await asyncio.gather(self._task, return_exceptions=True)
                    return
            if self._task is not None:
                if self._task.cancelled():
                    return
                await asyncio.shield(self._task)
        except asyncio.CancelledError:
            if self._task is not None:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
            raise
        except Exception as exc:
            await self._fail(exc)
            if self._task is not None:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)


def pcm_wav(pcm):
    target = io.BytesIO()
    with wave.open(target, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(pcm)
    return target.getvalue()


class WhisperWindowStream:
    """Capture continues while one serial worker invokes six-second windows."""

    def __init__(
        self, *, endpoint_name, language, chunk_ms, region,
        transcriber=None, max_pending_windows=4,
    ):
        if language not in ("ko-KR", "en-US") or chunk_ms != 6000:
            raise ValueError("Whisper comparison requires known language and six-second windows")
        provider_settings("whisper", endpoint=endpoint_name, region=region, whisper_mode="batch")
        if max_pending_windows < 1:
            raise ValueError("Whisper queue must have positive capacity")
        self.language = language
        self._transcribe = transcriber or stt.transcribe_audio
        self._max_pending = max_pending_windows
        self._window_bytes = 6 * PCM_BYTES_PER_SECOND
        self._pending = deque()
        self._buffer = bytearray()
        self._wake = asyncio.Event()
        self._task = None
        self._closing = False
        self.error = None

    def start(self, on_event):
        if self._task is not None:
            raise RuntimeError("Whisper capture was already started")
        self._handler = on_event
        self._task = asyncio.create_task(self._run())

    async def _fail(self, exc):
        if self.error is None:
            self.error = exc
            await self._handler({"type": "error", "message": str(exc) or type(exc).__name__})

    async def _run(self):
        number = 0
        try:
            await self._handler({"type": "ready", "stream_id": 1})
            while self.error is None:
                if self._pending:
                    pcm = self._pending.popleft()
                elif self._closing:
                    if not self._buffer:
                        break
                    pcm = bytes(self._buffer)
                    self._buffer.clear()
                else:
                    self._wake.clear()
                    await self._wake.wait()
                    continue
                result = await self._transcribe("whisper", pcm_wav(pcm), self.language[:2])
                number += 1
                await self._handler({
                    "type": "final", "stream_id": 1, "utterance_id": number,
                    "text": result.text, "language": result.language or self.language,
                })
            if self.error is None:
                await self._handler({"type": "ended", "stream_id": 1})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._fail(exc)

    async def send_audio(self, pcm):
        if self.error:
            raise self.error
        if self._closing or self._task is None:
            raise RuntimeError("Whisper capture is not accepting audio")
        if len(pcm) % 2:
            raise ValueError("PCM16 requires complete samples")
        self._buffer.extend(pcm)
        while len(self._buffer) >= self._window_bytes:
            if len(self._pending) >= self._max_pending:
                exc = RuntimeError("Whisper inference queue cannot keep up with capture")
                await self._fail(exc)
                raise exc
            self._pending.append(bytes(self._buffer[:self._window_bytes]))
            del self._buffer[:self._window_bytes]
            self._wake.set()

    async def close(self):
        self._closing = True
        self._wake.set()
        if self._task is None:
            return
        if self.error:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            return
        try:
            await asyncio.shield(self._task)
        except asyncio.CancelledError:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            raise


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower, upper = math.floor(index), math.ceil(index)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def caption_metrics(row):
    latest, finalized, times = {}, set(), []
    ended = None
    for event in row["events"]:
        kind = event.get("type")
        at = event.get("audio_elapsed_seconds")
        if kind == "ended" and at is not None:
            ended = at
        if kind not in ("partial", "final") or at is None:
            continue
        identity = (event.get("stream_id"), event.get("utterance_id"))
        if identity in finalized:
            continue
        if kind == "final":
            finalized.add(identity)
        text = (event.get("text") or "").strip()
        if text and text != latest.get(identity):
            latest[identity] = text
            times.append(at)
    intervals = [round(right - left, 6) for left, right in zip(times, times[1:])]
    return {
        "first_caption_seconds": times[0] if times else None,
        "caption_update_count": len(times),
        "caption_update_times_seconds": times,
        "caption_update_intervals_seconds": intervals,
        "caption_update_interval_median_seconds": statistics.median(intervals) if intervals else None,
        "caption_update_interval_p90_seconds": percentile(intervals, 0.9),
        "ended_seconds": ended,
        "missing_final_caption": row.get("final_count", 0) == 0,
    }


def summarize_comparison(rows, planned):
    result = summarize(rows, planned)
    valid = [row for row in rows if not row["error_count"]]
    first = [row["first_caption_seconds"] for row in valid if row["first_caption_seconds"] is not None]
    intervals = [value for row in valid for value in row["caption_update_intervals_seconds"]]
    timing = {
        "median_first_caption_seconds": statistics.median(first) if first else None,
        "p90_first_caption_seconds": percentile(first, 0.9),
        "caption_update_interval_median_seconds": statistics.median(intervals) if intervals else None,
        "caption_update_interval_p90_seconds": percentile(intervals, 0.9),
    }
    result["valid_only"].update(timing)
    result.update({key: value if result["ranking_allowed"] else None for key, value in timing.items()})
    result["samples_without_final_caption"] = sum(row["missing_final_caption"] for row in rows)
    return result


def actual_parameters(provider, language, settings):
    if provider == "transcribe":
        return transcribe_parameters(language)
    if provider == "whisper" and settings["mode"] == "serial_batch_windows":
        return {
            "batch_window_ms": 6000, "max_pending_windows": 4,
            "language": {"ko-KR": "korean", "en-US": "english"}[language],
            "task": "transcribe", "content_type": "application/json",
        }
    return {"type": "start", "sample_rate": 16000, "language": language, "chunk_ms": settings["chunk_ms"]}


async def run_benchmark(args, *, stream_factory=None, clock=time.perf_counter, sleep=asyncio.sleep):
    settings = provider_settings(
        args.provider, endpoint=args.endpoint, region=args.region,
        whisper_mode=getattr(args, "whisper_mode", "streaming"),
    )
    batch_windows = settings["mode"] == "serial_batch_windows"
    uses_http2_worker = settings["transport"] == "sagemaker_bidirectional_http2"
    for value in (args.ready_timeout, args.close_timeout):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Timeouts must be finite and positive")
    manifest_path = args.manifest.resolve()
    manifest, manifest_hash, prepared = prepare_samples(manifest_path, args.limit)
    preflight = None
    if getattr(args, "preflight", None):
        path = args.preflight.resolve()
        raw = path.read_bytes()
        if not isinstance(json.loads(raw), dict):
            raise ValueError("Deployment preflight must be a JSON object")
        preflight = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}
    factories = {
        "qwen3-asr": QwenStream, "nemotron": NemotronStream,
        "transcribe": TranscribeStream,
        "whisper": WhisperWindowStream if batch_windows else WhisperStream,
    }
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": args.provider, "model_id": settings["model_id"],
        "jumpstart_artifact_version": settings["artifact_version"],
        "endpoint": settings["endpoint"], "region": settings["region"],
        "manifest": str(manifest_path), "manifest_sha256": manifest_hash,
        "source": manifest.get("source"), "license": manifest.get("license"),
        "preflight": preflight, "planned_samples": len(prepared), "status": "running",
        "conditions": {
            "mode": "realtime_capture", "recognition_mode": settings["mode"],
            "transport": settings["transport"], "decode_strategy": settings["decode_strategy"],
            "whisper_mode": settings.get("whisper_mode"),
            "model_chunk_ms": settings["chunk_ms"] if uses_http2_worker else None,
            "batch_window_ms": 6000 if batch_windows else None,
            "capture_frame_ms": 160, "language_mode": "known language from manifest",
            "input": "Identical SHA-256 verified 16 kHz mono PCM16 WAV; no harness trimming, gain changes or augmentation. Server VAD and endpointing remain model-specific",
            "pacing": "Await adapter ready; send each 160 ms frame at its simulated capture end",
            "first_caption": "First nonempty partial OR final, relative to audio capture start",
            "first_partial": "First nonempty partial; null when none occurs. Explicit Whisper batch mode emits finals only",
            "caption_updates": "Changed nonempty text per utterance; duplicate finals ignored; intervals within each clip include pauses",
            "percentiles": "Linear interpolation at (n-1)*p; undefined when no intervals exist",
            "final_lag": "Last unique nonempty final minus complete source WAV duration, including trailing silence; can be negative",
            "ended": "Ended acknowledgement relative to audio capture start",
            "end_to_end": "Capture start through stream close, including complete source duration",
            "startup": (
                "Measured separately and excluded from capture-relative timings; explicit Whisper batch ready initializes its capture buffer"
                if batch_windows else
                "Measured separately and excluded from capture-relative timings; ready acknowledges the opened streaming transport"
            ),
            "application_latency": "Model/API benchmark, excluding browser, application WebSocket, translation and persistence startup",
            "accuracy": "Finalized text only; English Unicode word WER and Korean normalized character CER",
            "missing_results": "Successful empty recognition counts as deletions and is flagged; transport errors are unscored",
            "warmup": "Deployment verification is external; no timed samples are discarded or reused from tuning",
            "sample_order": "Manifest order, serial within this provider",
            "http2_worker_python": (config.NEMOTRON_STREAM_PYTHON or sys.executable) if uses_http2_worker else None,
            "ready_timeout_seconds": args.ready_timeout, "close_timeout_seconds": args.close_timeout,
        },
        "results": [],
    }
    report["summary"] = summarize_comparison([], len(prepared))
    save_report(args.output, report)
    for sample, pcm in prepared:
        row = await measure_clip(
            sample, pcm, endpoint=settings["endpoint"], region=settings["region"],
            chunk_ms=settings["chunk_ms"], auto=False,
            ready_timeout=args.ready_timeout, close_timeout=args.close_timeout,
            stream_factory=stream_factory or factories[args.provider], clock=clock, sleep=sleep,
        )
        row.update(caption_metrics(row))
        row.update(
            provider=args.provider, audio=sample["audio"], sha256=sample["sha256"], sha256_verified=True,
            parameters=actual_parameters(args.provider, row["requested_language"], settings),
        )
        report["results"].append(row)
        report["summary"] = summarize_comparison(report["results"], len(prepared))
        if len(report["results"]) == len(prepared):
            report["status"] = "incomplete" if report["summary"]["failed_samples"] else "complete"
        save_report(args.output, report)
        outcome = row["error"] if row.get("error") else f"CER={row['cer']:.3f}"
        print(f"{args.provider} {sample['id']}: {outcome}", file=sys.stderr, flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=PROVIDERS, required=True)
    parser.add_argument(
        "--whisper-mode", choices=("streaming", "batch"), default="streaming",
        help="Whisper streaming by default; batch explicitly repeats six-second framing on the configured endpoint",
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--endpoint")
    parser.add_argument("--region")
    parser.add_argument("--preflight", type=Path, help="Verified deployment JSON to reference by SHA-256")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ready-timeout", type=float, default=45)
    parser.add_argument("--close-timeout", type=float, default=60)
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(run_benchmark(args))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    return int(report["summary"]["failed_samples"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
