"""Four providers share capture timing without inventing streaming captions."""
import argparse
import asyncio
import importlib
import io
import json
from types import SimpleNamespace
import wave

import pytest

from tests.test_nemotron_streaming_benchmark import Clock, Stream, clip, manifest_file


def module():
    return importlib.import_module("scripts.benchmark_stt_realtime")


def transcript(result_id, text, partial=False, language="en-US"):
    result = SimpleNamespace(
        result_id=result_id, is_partial=partial, language_code=language,
        alternatives=[SimpleNamespace(transcript=text)],
    )
    return SimpleNamespace(transcript=SimpleNamespace(results=[result]))


class TranscriptOutput:
    def __init__(self):
        self.events = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.events.get()
        if value is None:
            raise StopAsyncIteration
        if isinstance(value, Exception):
            raise value
        return value


class TranscriptInput:
    def __init__(self, output):
        self.output = output
        self.audio = []
        self.ends = 0

    async def send_audio_event(self, *, audio_chunk):
        self.audio.append(audio_chunk)
        if len(self.audio) == 1:
            await self.output.events.put(transcript("aws-first", "Hello", True))

    async def end_stream(self):
        self.ends += 1
        await self.output.events.put(transcript("aws-first", "Hello there."))
        await self.output.events.put(transcript("aws-first", "Hello there."))
        await self.output.events.put(transcript("aws-second", "Again.", language=None))
        await self.output.events.put(None)


def test_caption_timing_ignores_unchanged_hypotheses_but_keeps_repeated_new_utterances():
    benchmark = module()
    row = {
        "first_partial_seconds": 0.5, "final_count": 2,
        "events": [
            {"type": "ready", "audio_elapsed_seconds": None},
            {"type": "partial", "stream_id": 1, "utterance_id": 1, "text": "Hello", "audio_elapsed_seconds": 0.5},
            {"type": "partial", "stream_id": 1, "utterance_id": 1, "text": "Hello", "audio_elapsed_seconds": 0.6},
            {"type": "partial", "stream_id": 1, "utterance_id": 1, "text": "Hello there", "audio_elapsed_seconds": 1.0},
            {"type": "final", "stream_id": 1, "utterance_id": 1, "text": "Hello there", "audio_elapsed_seconds": 1.2},
            {"type": "final", "stream_id": 1, "utterance_id": 1, "text": "Hello there", "audio_elapsed_seconds": 1.3},
            {"type": "final", "stream_id": 1, "utterance_id": 2, "text": "Hello there", "audio_elapsed_seconds": 2.0},
            {"type": "ended", "audio_elapsed_seconds": 2.4},
        ],
    }
    result = benchmark.caption_metrics(row)
    assert result["first_caption_seconds"] == 0.5
    assert result["caption_update_times_seconds"] == [0.5, 1.0, 2.0]
    assert result["caption_update_intervals_seconds"] == [0.5, 1.0]
    assert result["caption_update_interval_median_seconds"] == 0.75
    assert result["caption_update_interval_p90_seconds"] == pytest.approx(0.95)
    assert result["ended_seconds"] == 2.4
    assert result["missing_final_caption"] is False


def test_missing_captions_and_single_final_do_not_invent_zero_latency_or_partials():
    benchmark = module()
    empty = benchmark.caption_metrics({"events": [], "final_count": 0})
    assert empty["first_caption_seconds"] is None
    assert empty["caption_update_interval_median_seconds"] is None
    assert empty["caption_update_interval_p90_seconds"] is None
    assert empty["missing_final_caption"] is True
    final = benchmark.caption_metrics({
        "final_count": 1,
        "events": [{"type": "final", "utterance_id": 1, "text": "Hello", "audio_elapsed_seconds": 6.3}],
    })
    assert final["first_caption_seconds"] == 6.3
    assert final["caption_update_count"] == 1
    assert final["caption_update_interval_p90_seconds"] is None


async def test_transcribe_waits_for_open_and_maps_stable_result_ids_with_app_parameters():
    benchmark = module()
    opened = asyncio.Event()
    ready = asyncio.Event()
    output = TranscriptOutput()
    input_stream = TranscriptInput(output)
    calls, events = [], []

    class Client:
        async def start_stream_transcription(self, **kwargs):
            calls.append(kwargs)
            await opened.wait()
            return SimpleNamespace(input_stream=input_stream, output_stream=output)

    regions = []

    def client_factory(*, region):
        regions.append(region)
        return Client()

    async def on_event(event):
        events.append(event)
        if event["type"] == "ready":
            ready.set()

    stream = benchmark.TranscribeStream(
        endpoint_name="transcribe-streaming", language="en-US", chunk_ms=None,
        region="us-east-1", client_factory=client_factory,
    )
    stream.start(on_event)
    await asyncio.sleep(0)
    assert not ready.is_set()
    opened.set()
    await asyncio.wait_for(ready.wait(), 0.5)
    await stream.send_audio(b"\x01\0" * 2560)
    await stream.close()
    await stream.close()
    assert regions == ["us-east-1"]
    assert calls == [{
        "language_code": "en-US", "media_sample_rate_hz": 16000,
        "media_encoding": "pcm", "show_speaker_label": True,
    }]
    assert input_stream.audio == [b"\x01\0" * 2560]
    assert input_stream.ends == 1
    captions = [event for event in events if event["type"] in ("partial", "final")]
    assert [(event["type"], event["utterance_id"], event["text"]) for event in captions] == [
        ("partial", 1, "Hello"), ("final", 1, "Hello there."), ("final", 2, "Again."),
    ]
    assert captions[-1]["language"] == "en-US"
    assert events[0]["type"] == "ready" and events[-1]["type"] == "ended"
    assert stream.error is None


async def test_transcribe_failure_is_explicit_and_close_is_safe_before_ready():
    benchmark = module()
    events = []

    class Client:
        async def start_stream_transcription(self, **kwargs):
            raise RuntimeError("service unavailable")

    async def record(event):
        events.append(event)

    stream = benchmark.TranscribeStream(
        endpoint_name="transcribe-streaming", language="ko-KR", chunk_ms=None,
        region="us-east-1", client_factory=lambda **_: Client(),
    )
    stream.start(record)
    await asyncio.sleep(0)
    await stream.close()
    assert str(stream.error) == "service unavailable"
    assert [event["type"] for event in events] == ["error"]


async def test_transcribe_ready_timeout_returns_a_failed_row_instead_of_cancelling_the_run():
    benchmark = module()

    class Client:
        async def start_stream_transcription(self, **kwargs):
            await asyncio.Future()

    row = await asyncio.wait_for(benchmark.measure_clip(
        clip(), b"\x01\0" * 800, endpoint="transcribe-streaming", chunk_ms=None,
        region="us-east-1", ready_timeout=0.01, close_timeout=0.1,
        stream_factory=lambda **kwargs: benchmark.TranscribeStream(
            **kwargs, client_factory=lambda **_: Client(),
        ),
    ), timeout=0.5)
    assert row["error_count"] == 1
    assert "Ready timeout" in row["error"]
    assert "cer" not in row


async def test_whisper_does_not_stall_capture_and_flushes_exact_short_tail_serially():
    benchmark = module()
    entered, release, ready = asyncio.Event(), asyncio.Event(), asyncio.Event()
    requests, events = [], []
    active = peak = 0

    async def transcribe(provider, audio, language):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        requests.append((provider, audio, language))
        if len(requests) == 1:
            entered.set()
            await release.wait()
        active -= 1
        return SimpleNamespace(text="First." if len(requests) == 1 else "Tail.", language="en")

    async def record(event):
        events.append(event)
        if event["type"] == "ready":
            ready.set()

    stream = benchmark.WhisperWindowStream(
        endpoint_name="whisper-large", language="en-US", chunk_ms=6000,
        region="us-east-1", transcriber=transcribe,
    )
    stream.start(record)
    await asyncio.wait_for(ready.wait(), 0.5)
    source = b"\x01\0" * (16000 * 6) + b"\x02\0" * 6400
    for offset in range(0, len(source), 5120):
        await stream.send_audio(source[offset:offset + 5120])
        await asyncio.sleep(0)
    await asyncio.wait_for(entered.wait(), 0.5)
    assert len(requests) == 1
    assert not any(event["type"] == "final" for event in events)
    closing = asyncio.create_task(stream.close())
    release.set()
    await asyncio.wait_for(closing, 0.5)
    await stream.close()
    decoded = []
    for provider, audio, language in requests:
        assert (provider, language) == ("whisper", "en")
        with wave.open(io.BytesIO(audio), "rb") as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
            decoded.append(wav.readframes(wav.getnframes()))
    assert [len(part) for part in decoded] == [192000, 12800]
    assert b"".join(decoded) == source
    assert peak == 1
    assert [event["type"] for event in events] == ["ready", "final", "final", "ended"]
    assert [event["utterance_id"] for event in events if event["type"] == "final"] == [1, 2]


async def test_whisper_queue_overload_is_reported_instead_of_blocking_capture():
    benchmark = module()
    entered, release, ready = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def transcribe(*_):
        entered.set()
        await release.wait()
        return SimpleNamespace(text="caption", language="en")

    async def record(event):
        if event["type"] == "ready":
            ready.set()

    stream = benchmark.WhisperWindowStream(
        endpoint_name="whisper-large", language="en-US", chunk_ms=6000,
        region="us-east-1", transcriber=transcribe, max_pending_windows=1,
    )
    stream.start(record)
    await ready.wait()
    window = b"\x01\0" * (16000 * 6)
    try:
        await stream.send_audio(window)
        await asyncio.wait_for(entered.wait(), 0.5)
        await stream.send_audio(window)
        with pytest.raises(RuntimeError, match="queue"):
            await asyncio.wait_for(stream.send_audio(window), 0.1)
        assert stream.error is not None
    finally:
        release.set()
        await stream.close()


def test_final_profiles_use_actual_regions_model_chunks_and_whisper_configuration():
    benchmark = module()
    qwen = benchmark.provider_settings("qwen3-asr")
    nemotron = benchmark.provider_settings("nemotron")
    whisper = benchmark.provider_settings("whisper")
    assert (qwen["region"], qwen["endpoint"], qwen["chunk_ms"]) == (
        "ap-northeast-2", "transmeet-qwen-streaming", 1000,
    )
    assert (nemotron["region"], nemotron["endpoint"], nemotron["chunk_ms"]) == (
        "ap-northeast-2", "transmeet-nemotron-streaming", 560,
    )
    assert (whisper["region"], whisper["endpoint"], whisper["chunk_ms"]) == (
        "us-east-1", "whisper-large", 1000,
    )
    assert whisper["mode"] == "periodic_utterance_streaming"
    assert whisper["model_id"] == "openai/whisper-large-v3-turbo"
    assert whisper["artifact_version"] == "1.0.0"
    batch = benchmark.provider_settings("whisper", whisper_mode="batch")
    assert (batch["chunk_ms"], batch["mode"]) == (6000, "serial_batch_windows")
    with pytest.raises(ValueError, match="declared"):
        benchmark.provider_settings("whisper", region="ap-northeast-2")
    with pytest.raises(ValueError, match="declared"):
        benchmark.provider_settings("whisper", endpoint="other-endpoint")


async def test_report_preserves_pacing_hashes_caption_metrics_and_failed_accuracy(tmp_path):
    benchmark = module()
    manifest = manifest_file(tmp_path, [clip(), {**clip(), "id": "failed"}])
    preflight = tmp_path / "preflight.json"
    preflight.write_text('{"status":"ready"}')
    settings = argparse.Namespace(
        provider="qwen3-asr", manifest=manifest, output=tmp_path / "report.json",
        endpoint=None, region=None, limit=None, ready_timeout=0.1, close_timeout=0.1,
        preflight=preflight,
    )
    clock = Clock()
    streams, parameters = [], []

    async def partial(stream, number):
        if number == 1:
            await stream.emit({"type": "partial", "stream_id": 1, "utterance_id": 1,
                               "text": "Hello", "language": "English"})

    async def final(stream):
        await clock.sleep(0.2)
        await stream.emit({"type": "final", "stream_id": 1, "utterance_id": 1,
                           "text": "Hello world", "language": "English"})

    def factory(**kwargs):
        parameters.append(kwargs)
        stream = Stream(
            clock, startup=1, on_audio=partial, on_close=final,
            startup_event={"type": "error", "message": "Unavailable"} if streams else None,
        )
        streams.append(stream)
        return stream

    report = await benchmark.run_benchmark(settings, stream_factory=factory, clock=clock, sleep=clock.sleep)
    assert [round(at - 101, 3) for at, _ in streams[0].sent] == [0.16, 0.32, 0.4]
    assert parameters[0] == {
        "endpoint_name": "transmeet-qwen-streaming", "language": "en-US",
        "chunk_ms": 1000, "region": "ap-northeast-2",
    }
    assert report["provider"] == "qwen3-asr"
    assert report["model_id"] == "Qwen/Qwen3-ASR-1.7B"
    assert report["conditions"]["capture_frame_ms"] == 160
    assert report["preflight"]["sha256"]
    good, failed = report["results"]
    assert good["first_caption_seconds"] == pytest.approx(0.16)
    assert good["first_partial_seconds"] == pytest.approx(0.16)
    assert good["final_lag_seconds"] == pytest.approx(0.2)
    assert good["caption_update_interval_median_seconds"] == pytest.approx(0.44)
    assert good["sha256_verified"] is True and good["sha256"]
    assert good["wer"] == good["cer"] == 0
    assert failed["error_count"] > 0
    assert "cer" not in failed and "wer" not in failed
    assert report["summary"]["cer"] is None and report["summary"]["wer"] is None
    assert report["status"] == "incomplete"
    assert json.loads(settings.output.read_text()) == report


async def test_whisper_batch_first_caption_is_final_and_no_partial_latency_is_invented():
    benchmark = module()
    clock = Clock()

    async def transcribe(*_):
        await clock.sleep(0.25)
        return SimpleNamespace(text="Hello world", language="en")

    row = await benchmark.measure_clip(
        clip(), b"\x01\0" * 6400, endpoint="whisper-large", chunk_ms=6000,
        region="us-east-1", stream_factory=lambda **kwargs: benchmark.WhisperWindowStream(
            **kwargs, transcriber=transcribe,
        ), clock=clock, sleep=clock.sleep,
    )
    row.update(benchmark.caption_metrics(row))
    assert row["error_count"] == 0
    assert row["first_partial_seconds"] is None
    assert row["first_caption_seconds"] == pytest.approx(0.65)
    assert row["final_lag_seconds"] == pytest.approx(0.25)
    assert row["caption_update_interval_p90_seconds"] is None
    assert row["wer"] == 0


async def test_whisper_default_selects_real_stream_adapter_and_records_partial_protocol(tmp_path, monkeypatch):
    benchmark = module()
    clock = Clock()
    calls, streams = [], []

    async def partial(stream, number):
        if number == 1:
            await stream.emit({"type": "partial", "stream_id": 1, "utterance_id": 1,
                               "text": "Hello", "language": "en"})

    async def final(stream):
        await stream.emit({"type": "final", "stream_id": 1, "utterance_id": 1,
                           "text": "Hello world", "language": "en"})

    def streaming_factory(**kwargs):
        calls.append(kwargs)
        stream = Stream(clock, startup=1, on_audio=partial, on_close=final)
        streams.append(stream)
        return stream

    def forbidden_batch(**_):
        raise AssertionError("Default Whisper run selected the six-second batch adapter")

    monkeypatch.setattr(benchmark, "WhisperStream", streaming_factory, raising=False)
    monkeypatch.setattr(benchmark, "WhisperWindowStream", forbidden_batch)
    settings = argparse.Namespace(
        provider="whisper", manifest=manifest_file(tmp_path, [clip()]),
        output=tmp_path / "streaming.json", endpoint=None, region=None, limit=None,
        ready_timeout=0.1, close_timeout=0.1, preflight=None,
    )
    report = await benchmark.run_benchmark(settings, clock=clock, sleep=clock.sleep)
    assert calls == [{
        "endpoint_name": "whisper-large", "region": "us-east-1",
        "language": "en-US", "chunk_ms": 1000,
    }]
    assert [round(at - 101, 3) for at, _ in streams[0].sent] == [0.16, 0.32, 0.4]
    assert report["conditions"]["whisper_mode"] == "streaming"
    assert report["conditions"]["recognition_mode"] == "periodic_utterance_streaming"
    assert report["conditions"]["transport"] == "sagemaker_bidirectional_http2"
    assert report["conditions"]["decode_strategy"] == "bounded_utterance_redecode"
    assert report["conditions"]["model_chunk_ms"] == 1000
    assert report["conditions"]["batch_window_ms"] is None
    assert report["conditions"]["http2_worker_python"]
    row = report["results"][0]
    assert row["parameters"] == {
        "type": "start", "sample_rate": 16000, "language": "en-US", "chunk_ms": 1000,
    }
    assert row["first_caption_seconds"] == row["first_partial_seconds"] == pytest.approx(0.16)
    assert row["cer"] == row["wer"] == 0


async def test_explicit_whisper_batch_mode_keeps_six_second_final_only_reporting(tmp_path, monkeypatch):
    benchmark = module()
    clock = Clock()
    calls = []

    async def final(stream):
        await clock.sleep(0.25)
        await stream.emit({"type": "final", "stream_id": 1, "utterance_id": 1,
                           "text": "Hello world", "language": "en"})

    def batch_factory(**kwargs):
        calls.append(kwargs)
        return Stream(clock, startup=0, on_close=final)

    def forbidden_stream(**_):
        raise AssertionError("Explicit batch run selected the streaming adapter")

    monkeypatch.setattr(benchmark, "WhisperWindowStream", batch_factory)
    monkeypatch.setattr(benchmark, "WhisperStream", forbidden_stream, raising=False)
    settings = argparse.Namespace(
        provider="whisper", whisper_mode="batch", manifest=manifest_file(tmp_path, [clip()]),
        output=tmp_path / "batch.json", endpoint=None, region=None, limit=None,
        ready_timeout=0.1, close_timeout=0.1, preflight=None,
    )
    report = await benchmark.run_benchmark(settings, clock=clock, sleep=clock.sleep)
    assert calls[0]["chunk_ms"] == 6000
    assert report["conditions"]["whisper_mode"] == "batch"
    assert report["conditions"]["recognition_mode"] == "serial_batch_windows"
    assert report["conditions"]["transport"] == "sagemaker_request_response"
    assert report["conditions"]["model_chunk_ms"] is None
    assert report["conditions"]["batch_window_ms"] == 6000
    assert report["conditions"]["http2_worker_python"] is None
    row = report["results"][0]
    assert row["parameters"]["task"] == "transcribe"
    assert row["parameters"]["batch_window_ms"] == 6000
    assert row["first_partial_seconds"] is None
    assert row["first_caption_seconds"] == pytest.approx(0.65)


def test_whisper_streaming_settings_follow_declared_endpoint_region_and_interval(monkeypatch):
    benchmark = module()
    monkeypatch.setattr(benchmark.config, "WHISPER_STREAMING_ENDPOINT", "verified-whisper-stream")
    monkeypatch.setattr(benchmark.config, "WHISPER_STREAMING_REGION", "us-west-2")
    monkeypatch.setattr(benchmark.config, "WHISPER_STREAMING_CHUNK_MS", 2000)
    stream = benchmark.provider_settings("whisper")
    assert (stream["endpoint"], stream["region"], stream["chunk_ms"]) == (
        "verified-whisper-stream", "us-west-2", 2000,
    )
    batch = benchmark.provider_settings("whisper", whisper_mode="batch")
    assert (batch["endpoint"], batch["region"], batch["chunk_ms"]) == (
        "whisper-large", "us-east-1", 6000,
    )
    monkeypatch.setattr(benchmark.config, "WHISPER_STREAMING_CHUNK_MS", 6000)
    with pytest.raises(ValueError, match="1000"):
        benchmark.provider_settings("whisper")


def test_cli_accepts_explicit_historical_whisper_batch_mode(tmp_path, monkeypatch):
    benchmark = module()
    captured = []

    async def run(args):
        captured.append(args.whisper_mode)
        return {"summary": {"failed_samples": 0}}

    monkeypatch.setattr(benchmark, "run_benchmark", run)
    assert benchmark.main([
        "--provider", "whisper", "--whisper-mode", "batch",
        "--manifest", str(tmp_path / "manifest.json"), "--output", str(tmp_path / "out.json"),
    ]) == 0
    assert captured == ["batch"]
