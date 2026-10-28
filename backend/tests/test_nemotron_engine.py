"""Behavioral tests for the GPU container's streaming boundaries (no GPU needed)."""
import asyncio
import importlib
import queue
import struct
import sys
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def engine():
    return importlib.import_module("inference.nemotron_streaming.engine")


def pcm(samples, value=5000):
    return struct.pack(f"<{samples}h", *([value] * samples))


def unpack(data):
    return list(struct.unpack(f"<{len(data) // 2}h", data))


def test_stream_windows_preserve_overlap_and_pad_only_the_final_tail(engine):
    windows = engine.PcmWindows(
        first_samples=10, next_samples=12, first_advance=8, advance=8, left_context=2,
    )
    first = list(windows.feed(struct.pack("<13h", *range(13))))
    second = list(windows.feed(struct.pack("<11h", *range(13, 24))))
    tail = list(windows.finish())
    assert [(unpack(w.pcm), w.first) for w in first + second + tail] == [
        (list(range(10)), True),
        (list(range(8, 20)), False),
        (list(range(16, 24)) + [0, 0, 0, 0], False),
    ]
    assert list(windows.finish()) == []


def test_short_first_window_is_not_dropped(engine):
    windows = engine.PcmWindows(
        first_samples=10, next_samples=12, first_advance=8, advance=8, left_context=2,
    )
    assert list(windows.feed(struct.pack("<3h", 100, 200, 300))) == []
    result = list(windows.finish())
    assert len(result) == 1
    assert unpack(result[0].pcm) == [100, 200, 300] + [0] * 7
    assert result[0].first is True


def test_overlap_without_new_audio_does_not_create_an_extra_window(engine):
    windows = engine.PcmWindows(
        first_samples=10, next_samples=12, first_advance=8, advance=8, left_context=2,
    )
    assert len(list(windows.feed(pcm(10)))) == 1
    assert list(windows.finish()) == []


def test_stream_window_memory_does_not_grow_with_recording_length(engine):
    windows = engine.PcmWindows(
        first_samples=10, next_samples=12, first_advance=8, advance=8, left_context=2,
    )
    for _ in range(1000):
        list(windows.feed(pcm(8)))
        assert windows.buffered_bytes < 24
    with pytest.raises(ValueError, match="PCM16"):
        list(windows.feed(b"\x00"))


def test_endpointing_keeps_audio_until_a_real_silence_boundary(engine):
    segmenter = engine.UtteranceSegmenter()
    events = segmenter.feed(pcm(1600))  # 100 ms of speech
    events += segmenter.feed(pcm(9600, 0))  # 600 ms pause
    assert [kind for kind, _ in events].count("start") == 1
    assert all(kind != "end" for kind, _ in events)
    events += segmenter.feed(pcm(1600, 0))
    assert [kind for kind, _ in events].count("end") == 1
    delivered = b"".join(data for kind, data in events if kind in ("start", "audio"))
    assert delivered == pcm(1600) + pcm(11200, 0)
    assert segmenter.finish() == []


def test_stop_preserves_a_sub_frame_tail_exactly_once(engine):
    segmenter = engine.UtteranceSegmenter()
    source = pcm(1681)
    events = segmenter.feed(source) + segmenter.finish()
    delivered = b"".join(data for kind, data in events if kind in ("start", "audio"))
    assert delivered == source
    assert [kind for kind, _ in events].count("end") == 1
    assert segmenter.finish() == []


def test_silence_does_not_open_a_decoder_and_preroll_is_bounded(engine):
    segmenter = engine.UtteranceSegmenter()
    for _ in range(1000):
        assert segmenter.feed(pcm(320, 0)) == []
    assert segmenter.buffered_bytes <= 6400
    assert segmenter.finish() == []


def test_very_short_speech_is_preserved_on_explicit_stop(engine):
    segmenter = engine.UtteranceSegmenter()
    source = pcm(480)  # 30 ms: below the normal speech-start debounce
    events = segmenter.feed(source) + segmenter.finish()
    delivered = b"".join(data for kind, data in events if kind in ("start", "audio"))
    assert delivered == source
    assert [kind for kind, _ in events].count("start") == 1
    assert [kind for kind, _ in events].count("end") == 1


@pytest.mark.parametrize("name", ["en-00.wav", "en-04.wav", "ko-06.wav"])
def test_quiet_real_speech_reaches_the_model_without_losing_nonzero_samples(engine, name):
    path = Path(__file__).resolve().parents[2] / "benchmarks/stt-ko-en-2026-10-28/audio" / name
    with wave.open(str(path), "rb") as wav:
        source = wav.readframes(wav.getnframes())
    segmenter = engine.UtteranceSegmenter()
    events = []
    for offset in range(0, len(source), 5120):
        events += segmenter.feed(source[offset:offset + 5120])
    events += segmenter.finish()
    forwarded = b"".join(data for kind, data in events if kind in ("start", "audio"))
    assert [sample for sample in unpack(forwarded) if sample] == [
        sample for sample in unpack(source) if sample
    ]


@pytest.mark.parametrize("amplitude,samples", [(1, 71), (12, 480), (20, 1681)])
def test_quiet_speech_needs_no_amplitude_calibration_before_admission(engine, amplitude, samples):
    segmenter = engine.UtteranceSegmenter()
    source = pcm(samples, amplitude)
    events = segmenter.feed(source) + segmenter.finish()
    assert b"".join(data for kind, data in events if kind in ("start", "audio")) == source


def test_continuous_speech_has_bounded_utterance_state(engine):
    segmenter = engine.UtteranceSegmenter()
    source = pcm(16000 * 31)
    events = segmenter.feed(source) + segmenter.finish()
    assert [kind for kind, _ in events].count("end") == 2
    delivered = b"".join(data for kind, data in events if kind in ("start", "audio"))
    assert delivered == source


def test_language_tags_and_decoder_control_tokens_are_removed(engine):
    assert engine.clean_transcript(
        "<blank>회의를 시작합니다.<ko-KR>", "auto", ["<blank>", "<pad>"],
    ) == ("회의를 시작합니다.", "ko-KR")
    assert engine.clean_transcript(
        "Let's start.", "en-US", ["<blank>"],
    ) == ("Let's start.", "en-US")
    assert engine.clean_transcript("회의", "auto", []) == ("회의", "")


@pytest.mark.parametrize("special_tokens", [["<blank>", "<pad>"], ["<blank>", "<pad>", "<ko-KR>"]])
def test_auto_language_survives_trailing_rnnt_blanks(engine, special_tokens):
    assert engine.clean_transcript(
        "<blank>회의를 시작합니다.<ko-KR><blank><blank><pad>", "auto", special_tokens,
    ) == ("회의를 시작합니다.", "ko-KR")


def test_internal_model_language_tags_never_leak_into_captions(engine):
    assert engine.clean_transcript(
        "합금은 두 금속의 혼합물이다.<ko-KR> 주기율표를 확인하자.<ko-KR><pad>",
        "auto", ["<pad>", "<ko-KR>"],
    ) == ("합금은 두 금속의 혼합물이다. 주기율표를 확인하자.", "ko-KR")


def test_generation_handles_isolate_mutable_state_but_share_weights(engine):
    weights = object()
    model = SimpleNamespace(weights=weights, generation_config=SimpleNamespace(max_length=20))
    first = engine.isolate_model(model)
    second = engine.isolate_model(model)
    first._prompt_ids = 14
    first.generation_config.max_length = 4096
    assert first.weights is weights and second.weights is weights
    assert not hasattr(second, "_prompt_ids")
    assert not hasattr(model, "_prompt_ids")
    assert second.generation_config.max_length == 20
    assert model.generation_config.max_length == 20


def test_independent_recordings_do_not_share_endpointing_state(engine):
    first = engine.UtteranceSegmenter()
    second = engine.UtteranceSegmenter()
    first.feed(pcm(1600))
    assert second.feed(pcm(16000, 0)) == []
    assert [kind for kind, _ in first.finish()] == ["end"]
    assert second.finish() == []


@pytest.mark.asyncio
async def test_cancelled_socket_does_not_release_a_still_running_gpu_worker(monkeypatch):
    runtime_module = importlib.import_module("inference.nemotron_streaming.model_runtime")
    gate = threading.Event()
    started = queue.Queue()

    def stalled_generate(self):
        started.put(True)
        gate.wait(5)
        return "done", "en-US"

    monkeypatch.setattr(runtime_module.StreamingUtterance, "_generate", stalled_generate)
    monkeypatch.setattr(runtime_module, "CANCEL_TIMEOUT", 0.01, raising=False)
    runtime = runtime_module.ModelRuntime(None, None, max_workers=2)
    first = runtime.start_utterance(language="en-US", chunk_ms=560, on_partial=lambda *_: None)
    second = runtime.start_utterance(language="ko-KR", chunk_ms=320, on_partial=lambda *_: None)
    try:
        await asyncio.to_thread(started.get, True, 1)
        await asyncio.to_thread(started.get, True, 1)
        await first.cancel()
        await second.cancel()
        assert not first.task.done() and not second.task.done()
        with pytest.raises(RuntimeError, match="capacity"):
            runtime.start_utterance(language="en-US", chunk_ms=560, on_partial=lambda *_: None)
    finally:
        gate.set()
        await asyncio.gather(first.task, second.task)
    third = runtime.start_utterance(language="en-US", chunk_ms=560, on_partial=lambda *_: None)
    assert await third.task == ("done", "en-US")
