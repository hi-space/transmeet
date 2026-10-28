#!/usr/bin/env python3
"""Pace verified Korean/English audio into Qwen and measure live captions."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import config
from app.nemotron_stream import QwenStream
from scripts.benchmark_nemotron_streaming import run_benchmark as measure


async def run_benchmark(args, *, stream_factory=None, clock=time.perf_counter, sleep=asyncio.sleep):
    return await measure(
        args, stream_factory=stream_factory or QwenStream, clock=clock, sleep=sleep,
        model_id="Qwen/Qwen3-ASR-1.7B", artifact_version="1.0.0", allowed_chunks=(1000, 2000),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--endpoint", default="transmeet-qwen-streaming")
    parser.add_argument("--chunk-ms", type=int, choices=(1000, 2000), default=1000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--region", default=config.QWEN_STREAMING_REGION or config.QWEN_ASR_REGION or "ap-northeast-2")
    parser.add_argument("--ready-timeout", type=float, default=45)
    parser.add_argument("--close-timeout", type=float, default=20)
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(run_benchmark(args))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    return int(report["summary"]["failed_samples"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
