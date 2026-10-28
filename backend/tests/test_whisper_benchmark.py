import pytest

from tests.test_nemotron_streaming_benchmark import Clock, Stream, args, clip, manifest_file


async def test_whisper_benchmark_measures_paced_partials_and_uses_whisper_identity(tmp_path):
    from scripts import benchmark_whisper_streaming as benchmark

    clock = Clock()
    calls = []
    streams = []

    async def partial(stream, number):
        if number == 1:
            await stream.emit({
                "type": "partial", "stream_id": 1, "utterance_id": 1,
                "text": "Hello", "language": "en",
            })

    async def final(stream):
        await clock.sleep(0.25)
        await stream.emit({
            "type": "final", "stream_id": 1, "utterance_id": 1,
            "text": "Hello world", "language": "en",
        })

    def factory(**kwargs):
        calls.append(kwargs)
        stream = Stream(clock, on_audio=partial, on_close=final)
        streams.append(stream)
        return stream

    manifest = manifest_file(tmp_path, [clip()])
    options = args(tmp_path, manifest, chunk_ms=1000)
    report = await benchmark.run_benchmark(
        options, stream_factory=factory, clock=clock, sleep=clock.sleep,
    )
    assert report["model_id"] == "openai/whisper-large-v3-turbo"
    assert report["jumpstart_artifact_version"] == "1.0.0"
    assert report["conditions"]["model_chunk_ms"] == 1000
    assert report["summary"]["wer"] == 0
    assert report["summary"]["ranking_allowed"]
    assert calls[0]["chunk_ms"] == 1000
    assert [round(at - 102, 3) for at, _ in streams[0].sent] == [0.16, 0.32, 0.4]
    assert report["results"][0]["first_partial_seconds"] == pytest.approx(0.16)
    assert report["results"][0]["final_lag_seconds"] == pytest.approx(0.25)


async def test_whisper_benchmark_rejects_other_model_chunk_sizes(tmp_path):
    from scripts import benchmark_whisper_streaming as benchmark

    options = args(tmp_path, tmp_path / "not-present.json", chunk_ms=560)
    with pytest.raises(ValueError, match="1000"):
        await benchmark.run_benchmark(options)
