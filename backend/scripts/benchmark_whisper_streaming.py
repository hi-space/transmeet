#!/usr/bin/env python3
"""Measure real-time Whisper subtitles using the shared verified-audio benchmark."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import config
from scripts.benchmark_nemotron_streaming import run_benchmark as measure


async def run_benchmark(args, *, stream_factory=None, clock=time.perf_counter, sleep=asyncio.sleep):
    if stream_factory is None:
        from app.nemotron_stream import WhisperStream
        stream_factory = WhisperStream
    return await measure(
        args, stream_factory=stream_factory, clock=clock, sleep=sleep,
        model_id="openai/whisper-large-v3-turbo", artifact_version="1.0.0",
        allowed_chunks=(1000, 2000),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--endpoint", default="transmeet-whisper-streaming")
    parser.add_argument("--region", default=getattr(config, "WHISPER_STREAMING_REGION", "") or config.REGION)
    parser.add_argument("--chunk-ms", type=int, choices=(1000, 2000), default=1000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--ready-timeout", type=float, default=45)
    parser.add_argument("--close-timeout", type=float, default=20)
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(run_benchmark(args))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return int(report["summary"]["failed_samples"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
