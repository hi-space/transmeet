"""Live timing must include capture pacing and retain failed/incomplete conditions."""
import argparse
import asyncio
import hashlib
import io
import json
import wave

import pytest

from scripts import benchmark_nemotron_streaming as benchmark


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        assert seconds >= 0
        self.now += seconds
        await asyncio.sleep(0)


class Stream:
    """External transport boundary; pacing, scoring and reporting stay real."""
    def __init__(self, clock, *, startup=2, startup_event=None, on_audio=None, on_close=None):
        self.clock = clock
        self.startup = startup
        self.startup_event = startup_event or {"type": "ready", "stream_id": 1}
        self.on_audio = on_audio
        self.on_close = on_close
        self.sent = []
        self.error = None
        self.close_calls = 0

    def start(self, handler):
        self.handler = handler
        self.task = asyncio.create_task(self._start())

    async def _start(self):
        await self.clock.sleep(self.startup)
        if self.startup_event["type"] != "never":
            await self.emit(self.startup_event)

    async def emit(self, event):
        if event["type"] == "error":
            self.error = RuntimeError(event["message"])
        await self.handler(event)

    async def send_audio(self, pcm):
        self.sent.append((self.clock(), pcm))
        if self.on_audio:
            await self.on_audio(self, len(self.sent))

    async def close(self):
        self.close_calls += 1
        await self.task
        if self.on_close:
            await self.on_close(self)
        await self.emit({"type": "ended", "stream_id": 1})


def clip(language="en", reference="Hello world"):
    return {"id": "clip", "language": language, "reference": reference}


def args(tmp_path, manifest, **changes):
    return argparse.Namespace(
        manifest=manifest, endpoint="streaming-test", chunk_ms=changes.get("chunk_ms", 560),
        output=tmp_path / "report.json", limit=changes.get("limit"),
        auto=changes.get("auto", False), region="us-east-1",
        ready_timeout=0.1, close_timeout=0.1,
    )


def manifest_file(tmp_path, samples):
    manifest_samples = []
    for index, sample in enumerate(samples):
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x01" * 6400)
        audio = output.getvalue()
        filename = f"audio-{index}.wav"
        (tmp_path / filename).write_bytes(audio)
        manifest_samples.append({
            **sample, "audio": filename, "audio_seconds": 0.4,
            "sha256": hashlib.sha256(audio).hexdigest(),
        })
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({
        "source": {"dataset": "fixture"}, "license": "CC-BY-4.0", "samples": manifest_samples,
    }))
    return path


async def test_capture_is_paced_after_ready_and_latency_excludes_startup():
    clock = Clock()

    async def partials(stream, number):
        if number in (1, 3):
            await clock.sleep(0.04 if number == 1 else 0.02)
            await stream.emit({
                "type": "partial", "stream_id": 1, "utterance_id": 1,
                "text": "Hello" if number == 1 else "Hello world", "language": "en-US",
            })

    async def final(stream):
        await clock.sleep(0.3)
        event = {"type": "final", "stream_id": 1, "utterance_id": 1,
                 "text": "Hello world", "language": "en-US"}
        await stream.emit(event)
        await clock.sleep(0.05)
        await stream.emit(event)
        await clock.sleep(0.03)

    stream = Stream(clock, on_audio=partials, on_close=final)
    options = []

    def factory(**kwargs):
        options.append(kwargs)
        return stream

    pcm = b"\x00\x01" * 6400
    row = await benchmark.measure_clip(
        clip(), pcm, endpoint="streaming-test", chunk_ms=320, region="us-west-2",
        stream_factory=factory, clock=clock, sleep=clock.sleep,
    )
    assert [(round(at - 102, 3), len(data)) for at, data in stream.sent] == [
        (0.16, 5120), (0.32, 5120), (0.4, 2560),
    ]
    assert b"".join(data for _, data in stream.sent) == pcm
    assert options == [{
        "endpoint_name": "streaming-test", "language": "en-US", "chunk_ms": 320, "region": "us-west-2",
    }]
    assert row["startup_seconds"] == pytest.approx(2)
    assert row["first_partial_seconds"] == pytest.approx(0.2)
    assert row["final_lag_seconds"] == pytest.approx(0.32)
    assert row["end_to_end_seconds"] == pytest.approx(0.8)
    assert row["total_seconds"] == pytest.approx(2.8)
    assert row["partial_before_audio_end"] is True
    assert row["partial_count"] == 2
    assert row["final_count"] == 1
    assert row["text"] == "Hello world"
    assert row["cer"] == row["wer"] == 0
    assert row["error_count"] == 0
    assert row["events"][0]["audio_elapsed_seconds"] is None
    assert row["events"][1]["audio_elapsed_seconds"] == pytest.approx(0.2)
    assert stream.close_calls == 1


async def test_final_before_trailing_silence_keeps_negative_lag_and_rotated_ids():
    clock = Clock()

    async def utterances(stream, number):
        if number == 1:
            await stream.emit({"type": "final", "stream_id": 1, "utterance_id": 1,
                               "text": "Yes", "language": "en-US"})
        if number == 2:
            await stream.emit({"type": "final", "stream_id": 2, "utterance_id": 1,
                               "text": "yes", "language": "en-US"})

    stream = Stream(clock, on_audio=utterances)
    row = await benchmark.measure_clip(
        clip(reference="Yes yes"), b"\x00\x01" * 6400, endpoint="streaming-test",
        stream_factory=lambda **kwargs: stream, clock=clock, sleep=clock.sleep,
    )
    assert row["text"] == "Yes yes", "Different utterances can legitimately repeat the same words"
    assert row["final_count"] == 2
    assert row["final_lag_seconds"] == pytest.approx(-0.08)
    assert row["first_partial_seconds"] is None
    assert row["partial_before_audio_end"] is False
    assert row["wer"] == 0


def test_english_wer_uses_unicode_words_and_removes_apostrophes_without_rewriting_numbers():
    assert benchmark.score_words("ＤＯＮ’Ｔ stop: 3-2, café.", "dont stop 3 2 café") == {
        "word_errors": 0, "reference_words": 5, "wer": 0,
    }
    assert benchmark.score_words("one two three", "one four three five") == {
        "word_errors": 2, "reference_words": 3, "wer": 2 / 3,
    }
    assert benchmark.score_words("one two three", "one three")["word_errors"] == 1
    assert benchmark.score_words("25", "twenty five")["word_errors"] == 2
    assert benchmark.score_words("", "speech")["wer"] is None


@pytest.mark.parametrize("startup_event", [
    {"type": "never"},
    {"type": "error", "message": "model unavailable"},
    {"type": "ended"},
])
async def test_ready_or_error_wait_is_bounded_and_always_closes(startup_event):
    clock = Clock()
    stream = Stream(clock, startup=0, startup_event=startup_event)
    row = await asyncio.wait_for(benchmark.measure_clip(
        clip(), b"\x00\x01" * 800, endpoint="streaming-test",
        ready_timeout=0.01, close_timeout=0.1,
        stream_factory=lambda **kwargs: stream, clock=clock, sleep=clock.sleep,
    ), timeout=0.5)
    assert row["error_count"] == 1
    assert row.get("error")
    assert "cer" not in row
    assert stream.sent == []
    assert stream.close_calls == 1


async def test_a_failed_stream_does_not_score_speculative_text_or_count_one_error_twice():
    clock = Clock()

    async def fail(stream, number):
        await stream.emit({"type": "partial", "stream_id": 1, "utterance_id": 1,
                           "text": "Hello world", "language": "en-US"})
        await stream.emit({"type": "error", "message": "connection lost"})

    stream = Stream(clock, on_audio=fail)
    row = await benchmark.measure_clip(
        clip(), b"\x00\x01" * 6400, endpoint="streaming-test",
        stream_factory=lambda **kwargs: stream, clock=clock, sleep=clock.sleep,
    )
    assert row["text"] == ""
    assert row["partial_count"] == 1
    assert row["error_count"] == 1
    assert "cer" not in row
    assert len(stream.sent) == 1
    assert stream.close_calls == 1


async def test_a_close_that_never_finishes_becomes_an_explicit_failure():
    clock = Clock()

    async def hang(stream):
        await asyncio.Future()

    stream = Stream(clock, on_close=hang)
    row = await asyncio.wait_for(benchmark.measure_clip(
        clip(), b"\x00\x01" * 800, endpoint="streaming-test", close_timeout=0.01,
        stream_factory=lambda **kwargs: stream, clock=clock, sleep=clock.sleep,
    ), timeout=0.5)
    assert "close" in row["error"].lower()
    assert row["error_count"] == 1
    assert stream.close_calls == 1


async def test_cancelled_capture_still_closes_the_stream():
    clock = Clock()
    stream = Stream(clock)
    waiting = asyncio.Event()

    async def wait_for_capture(seconds):
        waiting.set()
        await asyncio.Future()

    task = asyncio.create_task(benchmark.measure_clip(
        clip(), b"\x00\x01" * 800, endpoint="streaming-test",
        stream_factory=lambda **kwargs: stream, clock=clock, sleep=wait_for_capture,
    ))
    await asyncio.wait_for(waiting.wait(), timeout=0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.close_calls == 1


async def test_an_audio_send_that_hangs_is_bounded_before_cleanup():
    clock = Clock()

    async def hang(stream, number):
        await asyncio.Future()

    stream = Stream(clock, on_audio=hang)
    row = await asyncio.wait_for(benchmark.measure_clip(
        clip(), b"\x00\x01" * 800, endpoint="streaming-test", close_timeout=0.01,
        stream_factory=lambda **kwargs: stream, clock=clock, sleep=clock.sleep,
    ), timeout=0.5)
    assert "audio send" in row["error"].lower()
    assert row["error_count"] == 1
    assert stream.close_calls == 1


def test_complete_corpus_weights_edits_and_excludes_missing_partial_latencies():
    common = {
        "error_count": 0, "partial_count": 0, "partial_before_audio_end": False,
        "first_partial_seconds": None, "startup_seconds": 0.2,
        "final_lag_seconds": -0.1, "end_to_end_seconds": 1.5,
    }
    rows = [
        {**common, "errors": 1, "reference_chars": 10, "word_errors": 2, "reference_words": 4},
        {**common, "errors": 0, "reference_chars": 90, "word_errors": 0, "reference_words": 6,
         "partial_count": 2, "partial_before_audio_end": True, "first_partial_seconds": 0.3,
         "final_lag_seconds": 0.3, "end_to_end_seconds": 2.5},
    ]
    summary = benchmark.summarize(rows, 2)
    assert summary["ranking_allowed"] is True
    assert summary["cer"] == pytest.approx(0.01)
    assert summary["wer"] == pytest.approx(0.2)
    assert summary["valid_only"]["median_first_partial_seconds"] == pytest.approx(0.3)
    assert summary["valid_only"]["median_final_lag_seconds"] == pytest.approx(0.1)
    assert summary["valid_only"]["median_end_to_end_seconds"] == pytest.approx(2)
    assert summary["valid_only"]["partial_before_audio_end_samples"] == 1
    assert benchmark.summarize(rows[:1], 2)["ranking_allowed"] is False


async def test_every_selected_audio_hash_is_verified_before_opening_any_stream(tmp_path):
    path = manifest_file(tmp_path, [clip(), {**clip(), "id": "second"}])
    (tmp_path / "audio-1.wav").write_bytes(b"tampered")
    opened = []
    with pytest.raises(ValueError, match="SHA-256"):
        await benchmark.run_benchmark(
            args(tmp_path, path), stream_factory=lambda **kwargs: opened.append(kwargs),
        )
    assert opened == []
    assert not (tmp_path / "report.json").exists()


async def test_incremental_output_keeps_failed_rows_and_cannot_rank_an_incomplete_run(tmp_path):
    path = manifest_file(tmp_path, [
        clip(language="ko", reference="안녕"), {**clip(), "id": "second"},
    ])
    options = args(tmp_path, path)
    clock = Clock()
    calls = []
    snapshots = []

    async def final(stream):
        await stream.emit({"type": "final", "stream_id": 1, "utterance_id": 1,
                           "text": "Hello word", "language": "en-US"})

    def factory(**kwargs):
        calls.append(kwargs)
        snapshots.append(json.loads(options.output.read_text()))
        if len(calls) == 1:
            return Stream(clock, startup_event={"type": "error", "message": "unavailable"})
        return Stream(clock, on_close=final)

    report = await benchmark.run_benchmark(
        options, stream_factory=factory, clock=clock, sleep=clock.sleep,
    )
    assert [call["language"] for call in calls] == ["ko-KR", "en-US"]
    assert len(snapshots[1]["results"]) == 1
    assert snapshots[1]["results"][0]["error"] == "unavailable"
    assert snapshots[1]["summary"]["ranking_allowed"] is False
    assert report["summary"]["successful_samples"] == 1
    assert report["summary"]["failed_samples"] == 1
    assert report["summary"]["cer"] is None
    assert report["summary"]["wer"] is None
    assert report["summary"]["valid_only"]["cer"] == pytest.approx(0.1)
    assert report["summary"]["valid_only"]["wer"] == pytest.approx(0.5)
    assert report["summary"]["ranking_allowed"] is False
    assert report["status"] == "incomplete"
    assert json.loads(options.output.read_text()) == report


async def test_limit_and_auto_apply_to_verified_inputs_without_calling_other_clips(tmp_path):
    path = manifest_file(tmp_path, [clip(), {**clip(), "id": "unused"}])
    options = args(tmp_path, path, limit=1, auto=True, chunk_ms=320)
    clock = Clock()
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return Stream(clock)

    report = await benchmark.run_benchmark(
        options, stream_factory=factory, clock=clock, sleep=clock.sleep,
    )
    assert len(calls) == 1
    assert calls[0]["language"] == "auto"
    assert calls[0]["chunk_ms"] == 320
    assert report["planned_samples"] == 1
    assert report["results"][0]["sha256_verified"] is True
    expected_hash = hashlib.sha256((tmp_path / "audio-0.wav").read_bytes()).hexdigest()
    assert report["results"][0]["sha256"] == expected_hash
    assert report["results"][0]["cer"] == 1, "A valid empty recognition still counts its deletions"
    assert report["status"] == "complete"
    assert report["summary"]["ranking_allowed"] is True
