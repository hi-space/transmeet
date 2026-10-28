import json

import pytest

from tests.test_nemotron_streaming_benchmark import Clock, Stream, args, clip, manifest_file


async def test_qwen_report_uses_real_capture_pacing_model_identity_and_final_accuracy(tmp_path):
    from scripts import benchmark_qwen_streaming as benchmark

    clock = Clock()
    options = []
    streams = []

    async def partial(stream, number):
        if number == 1:
            await stream.emit({
                "type": "partial", "stream_id": 1, "utterance_id": 1,
                "text": "Hello", "language": "English",
            })

    async def final(stream):
        await clock.sleep(0.25)
        await stream.emit({
            "type": "final", "stream_id": 1, "utterance_id": 1,
            "text": "Hello world", "language": "English",
        })

    def factory(**kwargs):
        options.append(kwargs)
        stream = Stream(clock, on_audio=partial, on_close=final)
        streams.append(stream)
        return stream

    manifest = manifest_file(tmp_path, [clip()])
    settings = args(tmp_path, manifest, chunk_ms=1000)
    settings.region = "ap-northeast-2"
    report = await benchmark.run_benchmark(
        settings, stream_factory=factory, clock=clock, sleep=clock.sleep,
    )
    assert report["model_id"] == "Qwen/Qwen3-ASR-1.7B"
    assert report["jumpstart_artifact_version"] == "1.0.0"
    assert report["conditions"]["model_chunk_ms"] == 1000
    assert report["summary"]["cer"] == report["summary"]["wer"] == 0
    assert report["summary"]["ranking_allowed"]
    assert options[0]["chunk_ms"] == 1000
    assert options[0]["region"] == "ap-northeast-2"
    assert [round(at - 102, 3) for at, _ in streams[0].sent] == [0.16, 0.32, 0.4]
    row = report["results"][0]
    assert row["first_partial_seconds"] == pytest.approx(0.16)
    assert row["final_lag_seconds"] == pytest.approx(0.25)
    assert json.loads(settings.output.read_text())["model_id"] == report["model_id"]


async def test_qwen_benchmark_rejects_nemotron_chunk_sizes_before_inference(tmp_path):
    from scripts import benchmark_qwen_streaming as benchmark

    settings = args(tmp_path, tmp_path / "absent.json", chunk_ms=560)
    with pytest.raises(ValueError, match="1000"):
        await benchmark.run_benchmark(settings)
