"""Qwen stream lifecycle and PCM ownership, without loading GPU weights."""
import asyncio
import importlib
import struct
import sys
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def pcm(samples, value=5000):
    return struct.pack(f"<{samples}h", *([value] * samples))


def runtime_module():
    return importlib.import_module("inference.qwen_streaming.model_runtime")


class StreamingModel:
    """The external synchronous inference boundary, with observable real audio."""

    def __init__(self):
        self.states = []
        self.calls = []
        self.running = 0
        self.peak_running = 0
        self.gate = None
        self.entered = threading.Event()

    def init_streaming_state(self, **settings):
        state = SimpleNamespace(
            **{**settings, "language": settings["language"] or "Korean"}, chunk_id=0, text="",
            buffer=np.zeros(0, dtype=np.float32), audio_accum=np.zeros(0, dtype=np.float32),
            _raw_decoded="",
        )
        self.states.append(state)
        return state

    def streaming_transcribe(self, audio, state):
        self.running += 1
        self.peak_running = max(self.peak_running, self.running)
        self.entered.set()
        try:
            if self.gate:
                assert self.gate.wait(3), "test inference gate timed out"
            time.sleep(0.002)
            state.buffer = np.concatenate([state.buffer, audio])
            size = round(state.chunk_size_sec * 16000)
            while len(state.buffer) >= size:
                state.audio_accum = np.concatenate([state.audio_accum, state.buffer[:size]])
                state.buffer = state.buffer[size:]
                state.chunk_id += 1
                state.text = f"회의 {state.chunk_id}" if state.language == "Korean" else f"Hello {state.chunk_id}"
                state._raw_decoded = state.text
            self.calls.append(("stream", state, len(audio)))
            return state
        finally:
            self.running -= 1

    def finish_streaming_transcribe(self, state):
        self.calls.append(("finish", state, len(state.buffer)))
        state.audio_accum = np.concatenate([state.audio_accum, state.buffer])
        state.buffer = np.zeros(0, dtype=np.float32)
        state.text = "회의를 시작합니다." if state.language == "Korean" else "Hello there."
        return state


@pytest.mark.asyncio
async def test_streaming_reuses_one_state_and_preserves_tail_before_final():
    module = runtime_module()
    model = StreamingModel()
    runtime = module.ModelRuntime(model)
    partials = []
    ready = asyncio.Event()
    loop = asyncio.get_running_loop()

    def partial(text, language):
        partials.append((text, language))
        loop.call_soon_threadsafe(ready.set)

    utterance = runtime.start_utterance(language="ko-KR", chunk_ms=1000, on_partial=partial)
    for _ in range(7):
        utterance.feed(pcm(2560))
    await asyncio.wait_for(ready.wait(), 2)
    assert partials == [("회의 1", "Korean")]
    assert not utterance.task.done()
    assert await utterance.finish() == ("회의를 시작합니다.", "Korean")
    assert len(model.states) == 1
    state = model.states[0]
    assert state.chunk_size_sec == 1
    assert len(state.audio_accum) == 17920
    assert np.all(state.audio_accum == np.float32(5000 / 32768))
    assert [kind for kind, *_ in model.calls].count("finish") == 1
    assert await utterance.finish() == ("회의를 시작합니다.", "Korean")
    assert [kind for kind, *_ in model.calls].count("finish") == 1


@pytest.mark.asyncio
async def test_sub_chunk_stop_and_auto_language_are_not_lost():
    model = StreamingModel()
    runtime = runtime_module().ModelRuntime(model)
    utterance = runtime.start_utterance(language="auto", chunk_ms=2000, on_partial=lambda *_: None)
    utterance.feed(pcm(321))
    assert await utterance.finish() == ("회의를 시작합니다.", "Korean")
    assert model.states[0].language == "Korean"
    assert model.states[0].chunk_size_sec == 2
    assert len(model.states[0].audio_accum) == 321


@pytest.mark.asyncio
async def test_two_sessions_have_independent_state_and_serialize_engine_calls():
    model = StreamingModel()
    runtime = runtime_module().ModelRuntime(model)
    korean = runtime.start_utterance(language="ko-KR", chunk_ms=1000, on_partial=lambda *_: None)
    english = runtime.start_utterance(language="en-US", chunk_ms=2000, on_partial=lambda *_: None)
    korean.feed(pcm(16000, 5000))
    english.feed(pcm(32000, 6000))
    assert await asyncio.gather(korean.finish(), english.finish()) == [
        ("회의를 시작합니다.", "Korean"), ("Hello there.", "English"),
    ]
    assert model.peak_running == 1
    assert len(model.states) == 2 and model.states[0] is not model.states[1]
    audios = {s.language: s.audio_accum for s in model.states}
    assert np.all(audios["Korean"] == np.float32(5000 / 32768))
    assert np.all(audios["English"] == np.float32(6000 / 32768))


class SplitKoreanTokenizer:
    # Captured from the pinned Qwen3-ASR-1.7B vocab.json + merges.txt.
    # Rolling five tokens back from these eight leaves "밝게 " plus the
    # first two UTF-8 bytes of 웃: the real upstream finalizer yields U+FFFD.
    pieces = {
        142176: "밝".encode(), 57801: "게".encode(), 85413: b" \xec\x9b",
        225: b"\x83", 38231: "습니다".encode(), 13: b".",
        138779: " 좋아".encode(), 35711: "요".encode(),
    }

    def encode(self, text):
        assert text == "밝게 웃습니다. 좋아요"
        return [142176, 57801, 85413, 225, 38231, 13, 138779, 35711]

    def decode(self, ids):
        return b"".join(self.pieces[i] for i in ids).decode("utf-8", errors="replace")


def test_final_tail_never_seeds_generation_with_split_korean_utf8():
    module = runtime_module()
    tokenizer = SplitKoreanTokenizer()
    state = SimpleNamespace(
        chunk_id=3, unfixed_chunk_num=2, unfixed_token_num=5,
        _raw_decoded="밝게 웃습니다. 좋아요", buffer=np.zeros(320), text="", language="Korean",
    )
    generated_prompts = []

    class UpstreamFinalizer:
        def finish_streaming_transcribe(self, state):
            # Exact rollback expression in qwen-asr 0.0.6's finalizer.
            prefix = ""
            if state.chunk_id >= state.unfixed_chunk_num:
                ids = tokenizer.encode(state._raw_decoded)
                prefix = tokenizer.decode(ids[:max(1, len(ids) - state.unfixed_token_num)])
            generated_prompts.append(prefix)
            state.text = prefix + "회의를 시작합니다."
            return state

    # Prove this fixture exercises the actual Unicode failure, not ASCII only.
    assert tokenizer.decode([142176, 57801, 85413]) == "밝게 \ufffd"
    result = module.finish_without_unsafe_prefix(UpstreamFinalizer(), state)
    assert generated_prompts == [""]
    assert result.text == "회의를 시작합니다."
    assert state.unfixed_chunk_num == 2
    assert module.MAX_NEW_TOKENS >= 512  # A reset must be able to decode 15 s.


@pytest.mark.asyncio
async def test_queue_overload_is_explicit_and_does_not_drop_accepted_audio():
    model = StreamingModel()
    model.gate = threading.Event()
    runtime = runtime_module().ModelRuntime(model, max_queued_bytes=32000)
    utterance = runtime.start_utterance(language="en-US", chunk_ms=1000, on_partial=lambda *_: None)
    try:
        utterance.feed(pcm(16000))
        await asyncio.to_thread(model.entered.wait, 1)
        utterance.feed(pcm(16000, 6000))
        with pytest.raises(RuntimeError, match="keep up"):
            utterance.feed(pcm(1))
        model.gate.set()
        assert await utterance.finish() == ("Hello there.", "English")
        assert len(model.states[0].audio_accum) == 32000
    finally:
        model.gate.set()
        await utterance.cancel()


@pytest.mark.asyncio
async def test_cancellation_retains_gpu_permit_until_synchronous_call_exits(monkeypatch):
    module = runtime_module()
    monkeypatch.setattr(module, "CANCEL_TIMEOUT", 0.01)
    model = StreamingModel()
    model.gate = threading.Event()
    runtime = module.ModelRuntime(model, max_workers=1)
    utterance = runtime.start_utterance(language="en-US", chunk_ms=1000, on_partial=lambda *_: None)
    try:
        utterance.feed(pcm(16000))
        assert await asyncio.to_thread(model.entered.wait, 1)
        await utterance.cancel()
        assert not utterance.task.done()
        with pytest.raises(RuntimeError, match="capacity"):
            runtime.start_utterance(language="ko-KR", chunk_ms=1000, on_partial=lambda *_: None)
    finally:
        model.gate.set()
        await asyncio.gather(utterance.task, return_exceptions=True)
    another = runtime.start_utterance(language="ko-KR", chunk_ms=1000, on_partial=lambda *_: None)
    another.feed(pcm(500))
    assert await another.finish() == ("회의를 시작합니다.", "Korean")


@pytest.mark.asyncio
async def test_invalid_pcm_and_unbounded_utterance_are_rejected():
    model = StreamingModel()
    runtime = runtime_module().ModelRuntime(model)
    utterance = runtime.start_utterance(language="en-US", chunk_ms=1000, on_partial=lambda *_: None)
    try:
        with pytest.raises(ValueError, match="PCM16"):
            utterance.feed(b"\x00")
        with pytest.raises(ValueError, match="15 seconds"):
            utterance.feed(pcm(16000 * 15 + 1))
    finally:
        await utterance.cancel()


def test_segmenter_preserves_short_tail_and_limits_continuous_speech():
    engine = importlib.import_module("inference.qwen_streaming.engine")
    segmenter = engine.UtteranceSegmenter()
    source = pcm(16000 * 31 + 71)
    events = segmenter.feed(source) + segmenter.finish()
    assert [kind for kind, _ in events].count("end") == 3
    assert b"".join(audio for kind, audio in events if kind != "end") == source
    assert segmenter.finish() == []


def test_silence_endpoint_and_idle_preroll_are_bounded():
    engine = importlib.import_module("inference.qwen_streaming.engine")
    segmenter = engine.UtteranceSegmenter()
    for _ in range(100):
        assert segmenter.feed(pcm(16000, 0)) == []
        assert segmenter.buffered_bytes <= 6400
    events = segmenter.feed(pcm(1600)) + segmenter.feed(pcm(12480, 0))
    assert all(kind != "end" for kind, _ in events)
    assert segmenter.feed(pcm(320, 0))[-1][0] == "end"


@pytest.mark.parametrize("filename", ["en-00.wav", "en-04.wav"])
def test_real_quiet_english_samples_are_forwarded_without_discarding_words(filename):
    engine = importlib.import_module("inference.qwen_streaming.engine")
    path = Path(__file__).resolve().parents[2] / "benchmarks/stt-ko-en-2026-10-28/audio" / filename
    with wave.open(str(path), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
        source = audio.readframes(audio.getnframes())
    segmenter = engine.UtteranceSegmenter()
    events = []
    for offset in range(0, len(source), 5120):
        events.extend(segmenter.feed(source[offset:offset + 5120]))
    events.extend(segmenter.finish())
    # These recordings contain quiet nonzero room tone between words. Their
    # English speech must not be removed by an absolute amplitude gate.
    assert b"".join(data for kind, data in events if kind != "end") == source


@pytest.mark.parametrize("value,samples", [(1, 1681), (12, 480), (20, 71)])
def test_immediate_quiet_speech_and_short_tails_are_preserved_without_calibration(value, samples):
    engine = importlib.import_module("inference.qwen_streaming.engine")
    source = pcm(samples, value)
    segmenter = engine.UtteranceSegmenter()
    events = segmenter.feed(source) + segmenter.finish()
    assert b"".join(data for kind, data in events if kind != "end") == source
    assert [kind for kind, _ in events].count("end") == 1


def test_single_quiet_frame_before_digital_silence_survives_debounce():
    engine = importlib.import_module("inference.qwen_streaming.engine")
    segmenter = engine.UtteranceSegmenter()
    source = pcm(320, 4)
    events = segmenter.feed(source)
    events += segmenter.feed(pcm(16000 * 2, 0))
    events += segmenter.finish()
    delivered = b"".join(data for kind, data in events if kind != "end")
    assert delivered.startswith(source)
    assert [kind for kind, _ in events].count("end") == 1


def test_relative_background_boundary_keeps_a_quiet_following_phrase():
    engine = importlib.import_module("inference.qwen_streaming.engine")
    segmenter = engine.UtteranceSegmenter()
    source = pcm(16000, 8) + pcm(16000, 500) + pcm(32000, 20)
    events = segmenter.feed(source)
    assert not any(kind == "end" for kind, _ in events)
    events += segmenter.feed(pcm(12800, 8))
    assert [kind for kind, _ in events].count("end") == 1
    assert b"".join(data for kind, data in events if kind != "end") == source + pcm(12800, 8)


def test_initial_quiet_room_tone_does_not_create_repeated_short_utterances():
    engine = importlib.import_module("inference.qwen_streaming.engine")
    path = Path(__file__).resolve().parents[2] / "benchmarks/stt-ko-en-2026-10-28/audio/en-00.wav"
    with wave.open(str(path), "rb") as audio:
        room_tone = audio.readframes(14400)  # First 900 ms, before the first word.
    segmenter = engine.UtteranceSegmenter()
    events = []
    for _ in range(3):
        events.extend(segmenter.feed(room_tone))
    assert not any(kind == "end" for kind, _ in events)
    events.extend(segmenter.finish())
    assert b"".join(data for kind, data in events if kind != "end") == room_tone * 3


class SamplingParams:
    """vLLM SamplingParams 의 clone/max_tokens 경계만 흉내 낸다."""

    def __init__(self, max_tokens):
        self.max_tokens = max_tokens
        self.temperature = 0.0

    def clone(self):
        return SamplingParams(self.max_tokens)


class BudgetModel(StreamingModel):
    def __init__(self):
        super().__init__()
        self.sampling_params = SamplingParams(512)
        self.budgets = []

    def streaming_transcribe(self, audio, state):
        before = state.chunk_id
        result = super().streaming_transcribe(audio, state)
        if state.chunk_id > before:
            self.budgets.append(("stream", self.sampling_params.max_tokens))
        return result

    def finish_streaming_transcribe(self, state):
        self.budgets.append(("finish", self.sampling_params.max_tokens))
        return super().finish_streaming_transcribe(state)


@pytest.mark.asyncio
async def test_each_decode_is_capped_by_the_audio_it_must_transcribe():
    """2026-11-09: 512 토큰 고정 상한 탓에 반복 환각이 청크마다 수 초씩 GPU 를 잡아
    'cannot keep up' 과 20초 확정 타임아웃이 났다. 공식 데모는 32 토큰을 쓴다."""
    module = runtime_module()
    model = BudgetModel()
    runtime = module.ModelRuntime(model)
    utterance = runtime.start_utterance(language="ko-KR", chunk_ms=1000, on_partial=lambda *_: None)
    try:
        for _ in range(55):  # 0.1초씩 5.5초 — 실제 브라우저처럼 잘게, 큐 한도 안에서
            utterance.feed(pcm(1600))
            await asyncio.sleep(0.005)
        await asyncio.wait_for(utterance.finish(), 5)
    finally:
        await utterance.cancel()
    stream = [budget for kind, budget in model.budgets if kind == "stream"]
    finish = [budget for kind, budget in model.budgets if kind == "finish"]
    # prefix 가 없는 첫 두 청크는 누적 오디오 전체를, 그다음은 새 청크만 받아 적는다.
    assert stream == [module.token_budget(1), module.token_budget(2)] + [module.token_budget(1)] * 3
    # 확정은 prefix 없이 5.5초 전체를 다시 받아 적는다.
    assert finish == [module.token_budget(5.5)]
    assert module.token_budget(1) <= 64
    assert module.token_budget(15) <= module.MAX_NEW_TOKENS
    # 정상 발화(초당 ~5토큰)는 잘리지 않을 만큼 넉넉해야 한다.
    assert module.token_budget(15) >= 15 * 12


def test_long_utterances_never_exceed_the_engine_limit():
    module = runtime_module()
    assert module.token_budget(60) == module.MAX_NEW_TOKENS
