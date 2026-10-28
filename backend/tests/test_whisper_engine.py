"""Whisper audio ownership, cumulative decoding and the Hugging Face boundary."""
import asyncio
import importlib
import struct
import sys
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def module():
    return importlib.import_module("inference.whisper_streaming.model_runtime")


def pcm(samples, value=5000):
    return struct.pack(f"<{samples}h", *([value] * samples))


async def wait_until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.002)
    await asyncio.wait_for(wait(), 2)


class Decoder:
    """Only the expensive synchronous model boundary is replaced."""

    def __init__(self):
        self.calls = []
        self.gate = None
        self.entered = threading.Event()
        self.running = 0
        self.peak_running = 0
        self.failure = None

    def decode(self, audio, language):
        self.running += 1
        self.peak_running = max(self.peak_running, self.running)
        self.calls.append((bytes(audio), language))
        self.entered.set()
        try:
            if self.gate is not None:
                assert self.gate.wait(3), "inference fixture did not unblock"
            if self.failure:
                raise self.failure
            name = "English" if language == "en-US" else "Korean"
            text = f"Hello {len(audio) // 2}" if name == "English" else f"회의 {len(audio) // 2}"
            return text, name
        finally:
            self.running -= 1


class WhisperModelBoundary:
    """Controlled Whisper model; VAD and generation policy remain separate."""

    def __init__(self):
        self.encoder_calls = 0
        self.encoder_output = None

    def get_encoder(self):
        def encode(features, *, return_dict):
            assert return_dict is True
            self.encoder_calls += 1
            self.encoder_output = SimpleNamespace(last_hidden_state=features)
            return self.encoder_output
        return encode

    def detect_language(self, *, encoder_outputs, generation_config):
        torch = pytest.importorskip("torch")
        assert encoder_outputs is self.encoder_output
        assert generation_config.language is None
        return torch.tensor([50264])


class SpeechProbability:
    """External VAD probability; the real adapter decides whether to decode."""

    def __init__(self, probability=0.99):
        self.probability = probability
        self.inputs = []

    def max_probability(self, audio):
        self.inputs.append(audio.copy())
        return self.probability


@pytest.mark.asyncio
async def test_partials_revise_the_whole_utterance_and_finish_reuses_last_decode():
    decoder = Decoder()
    runtime = module().ModelRuntime(decoder)
    partials = []
    utterance = runtime.start_utterance(
        language="ko-KR", chunk_ms=1000, on_partial=lambda *result: partials.append(result),
    )
    utterance.feed(pcm(16000))
    await wait_until(lambda: len(partials) == 1)
    assert partials == [("회의 16000", "Korean")]
    assert not utterance.task.done()
    utterance.feed(pcm(16000, 6000))
    await wait_until(lambda: len(partials) == 2)
    assert partials[-1] == ("회의 32000", "Korean")
    assert await utterance.finish() == ("회의 32000", "Korean")
    assert await utterance.finish() == ("회의 32000", "Korean")
    assert [audio for audio, _ in decoder.calls] == [
        pcm(16000), pcm(16000) + pcm(16000, 6000),
    ]


@pytest.mark.asyncio
async def test_final_flush_includes_a_short_tail_and_sub_interval_auto_audio():
    decoder = Decoder()
    runtime = module().ModelRuntime(decoder)
    partials = []
    utterance = runtime.start_utterance(
        language="en-US", chunk_ms=1000, on_partial=lambda *result: partials.append(result),
    )
    utterance.feed(pcm(16000))
    await wait_until(lambda: partials)
    utterance.feed(pcm(81, 7000))
    assert await utterance.finish() == ("Hello 16081", "English")
    assert decoder.calls[-1] == (pcm(16000) + pcm(81, 7000), "en-US")
    tiny = runtime.start_utterance(language="auto", chunk_ms=2000, on_partial=lambda *_: None)
    tiny.feed(pcm(321, 2))
    assert await tiny.finish() == ("회의 321", "Korean")
    assert decoder.calls[-1] == (pcm(321, 2), "auto")


@pytest.mark.asyncio
async def test_two_second_interval_does_not_decode_each_browser_frame():
    decoder = Decoder()
    runtime = module().ModelRuntime(decoder)
    partials = []
    utterance = runtime.start_utterance(
        language="en-US", chunk_ms=2000, on_partial=lambda *result: partials.append(result),
    )
    utterance.feed(pcm(16000))
    await asyncio.sleep(0.02)
    assert decoder.calls == []
    utterance.feed(pcm(16000))
    await wait_until(lambda: partials)
    assert await utterance.finish() == ("Hello 32000", "English")
    assert len(decoder.calls) == 1


@pytest.mark.asyncio
async def test_slow_inference_coalesces_updates_without_losing_accepted_audio():
    decoder = Decoder()
    decoder.gate = threading.Event()
    runtime = module().ModelRuntime(decoder)
    utterance = runtime.start_utterance(language="ko-KR", chunk_ms=1000, on_partial=lambda *_: None)
    first = pcm(16000)
    rest = pcm(16000, 6000) + pcm(16000, 7000) + pcm(16081, 8000)
    try:
        utterance.feed(first)
        assert await asyncio.to_thread(decoder.entered.wait, 1)
        for audio in (pcm(16000, 6000), pcm(16000, 7000), pcm(16081, 8000)):
            utterance.feed(audio)
        finish = asyncio.create_task(utterance.finish())
        await asyncio.sleep(0)
        decoder.gate.set()
        assert await finish == ("회의 64081", "Korean")
        assert [audio for audio, _ in decoder.calls] == [first, first + rest]
    finally:
        decoder.gate.set()
        await utterance.cancel()


@pytest.mark.asyncio
async def test_sessions_waiting_for_gpu_decode_the_latest_snapshot_and_keep_audio_separate():
    decoder = Decoder()
    decoder.gate = threading.Event()
    runtime = module().ModelRuntime(decoder)
    ko = runtime.start_utterance(language="ko-KR", chunk_ms=1000, on_partial=lambda *_: None)
    en = runtime.start_utterance(language="en-US", chunk_ms=1000, on_partial=lambda *_: None)
    try:
        ko.feed(pcm(16000, 5000))
        assert await asyncio.to_thread(decoder.entered.wait, 1)
        en.feed(pcm(16000, 6000))
        await asyncio.sleep(0.02)
        en.feed(pcm(16000, 7000))
        tasks = [asyncio.create_task(u.finish()) for u in (ko, en)]
        await asyncio.sleep(0)
        decoder.gate.set()
        assert await asyncio.gather(*tasks) == [
            ("회의 16000", "Korean"), ("Hello 32000", "English"),
        ]
        assert decoder.peak_running == 1
        assert decoder.calls == [
            (pcm(16000, 5000), "ko-KR"),
            (pcm(16000, 6000) + pcm(16000, 7000), "en-US"),
        ]
    finally:
        decoder.gate.set()
        await asyncio.gather(ko.cancel(), en.cancel())


@pytest.mark.asyncio
async def test_queue_overload_rejects_new_audio_without_dropping_accepted_audio():
    decoder = Decoder()
    decoder.gate = threading.Event()
    runtime = module().ModelRuntime(decoder, max_queued_bytes=32000)
    utterance = runtime.start_utterance(language="en-US", chunk_ms=1000, on_partial=lambda *_: None)
    try:
        utterance.feed(pcm(16000))
        assert await asyncio.to_thread(decoder.entered.wait, 1)
        utterance.feed(pcm(16000, 6000))
        with pytest.raises(RuntimeError, match="keep up"):
            utterance.feed(pcm(1, 7000))
        decoder.gate.set()
        assert await utterance.finish() == ("Hello 32000", "English")
        assert decoder.calls[-1][0] == pcm(16000) + pcm(16000, 6000)
    finally:
        decoder.gate.set()
        await utterance.cancel()


@pytest.mark.asyncio
async def test_cancel_keeps_capacity_reserved_until_synchronous_inference_exits(monkeypatch):
    runtime_module = module()
    monkeypatch.setattr(runtime_module, "CANCEL_TIMEOUT", 0.01)
    decoder = Decoder()
    decoder.gate = threading.Event()
    runtime = runtime_module.ModelRuntime(decoder, max_workers=1)
    partials = []
    utterance = runtime.start_utterance(
        language="ko-KR", chunk_ms=1000, on_partial=lambda *result: partials.append(result),
    )
    try:
        utterance.feed(pcm(16000))
        assert await asyncio.to_thread(decoder.entered.wait, 1)
        await utterance.cancel()
        assert not utterance.task.done()
        with pytest.raises(RuntimeError, match="capacity"):
            runtime.start_utterance(language="en-US", chunk_ms=1000, on_partial=lambda *_: None)
        decoder.gate.set()
        with pytest.raises(RuntimeError, match="cancelled"):
            await utterance.finish()
        assert partials == []
    finally:
        decoder.gate.set()
        await asyncio.gather(utterance.task, return_exceptions=True)
    another = runtime.start_utterance(language="en-US", chunk_ms=1000, on_partial=lambda *_: None)
    another.feed(pcm(500))
    assert await another.finish() == ("Hello 500", "English")


@pytest.mark.asyncio
async def test_cancelled_finish_waiter_does_not_cancel_the_worker_or_lose_final_audio():
    decoder = Decoder()
    decoder.gate = threading.Event()
    runtime = module().ModelRuntime(decoder, max_workers=1)
    utterance = runtime.start_utterance(language="auto", chunk_ms=2000, on_partial=lambda *_: None)
    try:
        utterance.feed(pcm(321))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(utterance.finish(), timeout=0.02)
        assert not utterance.task.done()
        decoder.gate.set()
        assert await utterance.finish() == ("회의 321", "Korean")
    finally:
        decoder.gate.set()
        await utterance.cancel()


@pytest.mark.asyncio
async def test_invalid_pcm_duration_and_feed_after_finish_are_explicit():
    runtime = module().ModelRuntime(Decoder())
    utterance = runtime.start_utterance(language="en-US", chunk_ms=1000, on_partial=lambda *_: None)
    with pytest.raises(ValueError, match="PCM16"):
        utterance.feed(b"\0")
    with pytest.raises(ValueError, match="15 seconds"):
        utterance.feed(pcm(240001))
    assert await utterance.finish() == ("", "English")
    with pytest.raises(RuntimeError, match="ended"):
        utterance.feed(pcm(1))


@pytest.mark.parametrize("language,chunk_ms", [
    ("invalid", 1000), ("ko-KR", 560), ("auto", True),
])
@pytest.mark.asyncio
async def test_invalid_start_does_not_consume_a_worker(language, chunk_ms):
    runtime = module().ModelRuntime(Decoder(), max_workers=1)
    with pytest.raises(ValueError):
        runtime.start_utterance(language=language, chunk_ms=chunk_ms, on_partial=lambda *_: None)
    valid = runtime.start_utterance(language="en-US", chunk_ms=1000, on_partial=lambda *_: None)
    assert await valid.finish() == ("", "English")


@pytest.mark.asyncio
async def test_batch_has_thirty_second_limit_and_shares_stream_capacity(monkeypatch):
    runtime_module = module()
    decoder = Decoder()
    runtime = runtime_module.ModelRuntime(decoder, max_workers=1)
    assert await runtime.transcribe(pcm(480000), language="en-US") == ("Hello 480000", "English")
    with pytest.raises(ValueError, match="30 seconds"):
        await runtime.transcribe(pcm(480001), language="en-US")
    monkeypatch.setattr(runtime_module, "CANCEL_TIMEOUT", 0.01)
    decoder.gate = threading.Event()
    decoder.entered.clear()
    pending = asyncio.create_task(runtime.transcribe(pcm(321), language="auto"))
    try:
        assert await asyncio.to_thread(decoder.entered.wait, 1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        with pytest.raises(RuntimeError, match="capacity"):
            runtime.start_utterance(language="en-US", chunk_ms=1000, on_partial=lambda *_: None)
    finally:
        decoder.gate.set()


@pytest.mark.asyncio
async def test_inference_failure_is_observable_and_releases_capacity():
    decoder = Decoder()
    decoder.failure = RuntimeError("model unavailable")
    runtime = module().ModelRuntime(decoder, max_workers=1)
    utterance = runtime.start_utterance(language="auto", chunk_ms=1000, on_partial=lambda *_: None)
    utterance.feed(pcm(16000))
    await wait_until(utterance.task.done)
    with pytest.raises(RuntimeError, match="model unavailable"):
        await utterance.finish()
    decoder.failure = None
    assert await runtime.transcribe(pcm(160), language="en-US") == ("Hello 160", "English")


def test_whisper_decoder_uses_model_dtype_transcribes_and_reads_detected_language_prefix():
    torch = pytest.importorskip("torch")
    seen = []

    class Processor:
        def __call__(self, audio, *, sampling_rate, return_tensors):
            assert sampling_rate == 16000 and return_tensors == "pt"
            assert audio.dtype == np.float32
            np.testing.assert_array_equal(audio, np.array([0.5, -0.5], dtype=np.float32))
            return SimpleNamespace(input_features=torch.zeros((1, 128, 3000)))

        def batch_decode(self, ids, *, skip_special_tokens):
            assert skip_special_tokens is True
            return ["  회의를 시작합니다.  " if ids[0, 1].item() == 50264 else "  Hello there.  "]

    class Model(WhisperModelBoundary):
        config = SimpleNamespace(max_target_positions=448)
        generation_config = SimpleNamespace(
            language="en", task="translate", forced_decoder_ids=[(1, 50259)],
            lang_to_id={"<|ko|>": 50264, "<|en|>": 50259}, eos_token_id=50257,
            decoder_start_token_id=50258, no_timestamps_token_id=50364,
        )

        def parameters(self):
            yield torch.zeros(1, dtype=torch.float16)

        def generate(self, input_features, **kwargs):
            seen.append(kwargs)
            assert input_features.dtype == torch.float16
            assert torch.is_inference_mode_enabled()
            config = kwargs["generation_config"]
            assert config is not self.generation_config
            assert config.forced_decoder_ids is None
            assert config.language is None  # Clear saved language before each request.
            assert kwargs["task"] == "transcribe"
            assert kwargs["max_new_tokens"] <= 444
            assert kwargs["encoder_outputs"] is self.encoder_output
            token = 50259 if kwargs.get("language") == "en" else 50264
            return torch.tensor([[50258, token, 50360, 50364, 100, 50257]])

    model = Model()
    decoder = module().WhisperDecoder(model, Processor(), SpeechProbability())
    source = struct.pack("<hh", 16384, -16384)
    assert decoder.decode(source, "en-US") == ("Hello there.", "English")
    assert decoder.decode(source, "auto") == ("회의를 시작합니다.", "Korean")
    assert decoder.decode(source, "ko-KR") == ("회의를 시작합니다.", "Korean")
    assert [call.get("language") for call in seen] == ["en", "ko", "ko"]
    assert model.encoder_calls == 3
    assert Model.generation_config.language == "en"
    assert Model.generation_config.task == "translate"


@pytest.mark.parametrize("decoded,include_eos,error", [
    ("잘못된 \ufffd 문자", True, "Unicode"),
    ("unfinished", False, "token limit"),
])
def test_whisper_decoder_rejects_corrupted_or_token_truncated_finals(decoded, include_eos, error):
    torch = pytest.importorskip("torch")

    class Processor:
        def __call__(self, *args, **kwargs):
            return SimpleNamespace(input_features=torch.zeros((1, 128, 3000)))

        def batch_decode(self, *args, **kwargs):
            return [decoded]

    class Model(WhisperModelBoundary):
        config = SimpleNamespace(max_target_positions=448)
        generation_config = SimpleNamespace(
            lang_to_id={"<|ko|>": 50264}, eos_token_id=50257,
            decoder_start_token_id=50258, no_timestamps_token_id=50364,
        )

        def parameters(self):
            yield torch.zeros(1, dtype=torch.float16)

        def generate(self, *args, **kwargs):
            ids = [50258, 50264, 50360, 50364, 100]
            return torch.tensor([ids + ([50257] if include_eos else [])])

    decoder = module().WhisperDecoder(Model(), Processor(), SpeechProbability())
    with pytest.raises(RuntimeError, match=error):
        decoder.decode(pcm(321), "auto")


@pytest.mark.parametrize("speech_probability,expected_text", [
    (0.01, ""), (0.49, ""), (0.5, "감사합니다."), (0.99, "감사합니다."),
])
def test_acoustic_vad_suppresses_noise_but_not_legitimate_short_thanks(
    speech_probability, expected_text, caplog,
):
    torch = pytest.importorskip("torch")

    class Processor:
        def __call__(self, audio, **kwargs):
            # Quiet audio still reaches the feature extractor unchanged.
            np.testing.assert_array_equal(audio, np.full(321, 2 / 32768, dtype=np.float32))
            return SimpleNamespace(input_features=torch.zeros((1, 128, 3000)))

        def batch_decode(self, *args, **kwargs):
            return ["감사합니다."]

    class Model(WhisperModelBoundary):
        config = SimpleNamespace(max_target_positions=448)
        generation_config = SimpleNamespace(
            lang_to_id={"<|ko|>": 50264, "<|en|>": 50259}, eos_token_id=50257,
            decoder_start_token_id=50258, no_timestamps_token_id=50364,
        )

        def __init__(self):
            super().__init__()
            self.generation_calls = 0

        def parameters(self):
            yield torch.zeros(1, dtype=torch.float16)

        def generate(self, input_features, **kwargs):
            self.generation_calls += 1
            return torch.tensor([[50258, 50264, 50360, 50364, 100, 50257]])

    model = Model()
    detector = SpeechProbability(speech_probability)
    decoder = module().WhisperDecoder(model, Processor(), detector)
    assert decoder.decode(pcm(321, 2), "auto") == (expected_text, "Korean" if expected_text else "")
    np.testing.assert_array_equal(detector.inputs[0], np.full(321, 2 / 32768, dtype=np.float32))
    assert model.encoder_calls == (1 if expected_text else 0)
    assert model.generation_calls == (1 if expected_text else 0)
    assert "vad_max_probability=" in caplog.text
    assert "audio_seconds=0.020" in caplog.text
    assert "감사합니다" not in caplog.text


@pytest.fixture(scope="module")
def acoustic_detector():
    pytest.importorskip("silero_vad")
    pytest.importorskip("onnxruntime")
    return importlib.import_module("inference.whisper_streaming.vad").SileroSpeechDetector.load()


def read_pcm_fixture(path):
    with wave.open(str(path)) as sound:
        assert (sound.getframerate(), sound.getnchannels(), sound.getsampwidth()) == (16000, 1, 2)
        return sound.readframes(sound.getnframes())


@pytest.mark.parametrize("filename", [
    f"{language}-{index:02}.wav" for language in ("en", "ko") for index in range(10)
])
def test_real_vad_retains_fleurs_speech_and_rejects_extra_noise_segments(acoustic_detector, filename):
    engine = importlib.import_module("inference.whisper_streaming.engine")
    root = Path(__file__).resolve().parents[2]
    source = read_pcm_fixture(root / "benchmarks/stt-ko-en-2026-10-28/audio" / filename)
    segmenter = engine.UtteranceSegmenter()
    events = []
    for offset in range(0, len(source), 5120):
        events.extend(segmenter.feed(source[offset:offset + 5120]))
    events.extend(segmenter.finish())
    noise_segments = {
        ("en-00.wav", 2), ("ko-00.wav", 2), ("ko-01.wav", 1), ("ko-01.wav", 3),
        ("ko-02.wav", 2), ("ko-04.wav", 2), ("ko-05.wav", 2), ("ko-06.wav", 1),
    }
    audio = bytearray()
    index = 0
    for kind, chunk in events:
        if kind in ("start", "audio"):
            audio.extend(chunk)
            continue
        index += 1
        signal = np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768
        probability = acoustic_detector.max_probability(signal)
        assert (probability >= 0.5) == ((filename, index) not in noise_segments), (filename, index, probability)
        audio = bytearray()


@pytest.mark.parametrize("filename", [
    f"{language}-{word}{suffix}.wav"
    for language in ("en", "ko") for word in ("thanks", "yes") for suffix in ("", "-quiet")
])
def test_real_vad_retains_normal_and_quiet_spoken_acknowledgements(acoustic_detector, filename):
    root = Path(__file__).resolve().parents[2]
    source = read_pcm_fixture(root / "benchmarks/whisper-streaming-2026-10-28/short-speech-audio" / filename)
    signal = np.frombuffer(source, dtype="<i2").astype(np.float32) / 32768
    assert acoustic_detector.max_probability(signal) >= 0.5


def test_real_vad_resets_state_between_utterances_and_rejects_silence(acoustic_detector):
    root = Path(__file__).resolve().parents[2]
    source = read_pcm_fixture(
        root / "benchmarks/whisper-streaming-2026-10-28/short-speech-audio/ko-yes-quiet.wav",
    )
    signal = np.frombuffer(source, dtype="<i2").astype(np.float32) / 32768
    first = acoustic_detector.max_probability(signal)
    assert acoustic_detector.max_probability(np.zeros(16000, dtype=np.float32)) < 0.5
    assert acoustic_detector.max_probability(np.zeros(81, dtype=np.float32)) < 0.5
    assert acoustic_detector.max_probability(signal) == first


def test_vad_pads_a_short_tail_without_truncating_or_rescaling_it():
    torch = pytest.importorskip("torch")
    vad = importlib.import_module("inference.whisper_streaming.vad")
    received = []

    class Model:
        def audio_forward(self, audio, rate):
            assert rate == 16000
            received.append(audio.numpy().copy())
            return torch.tensor([[0.75]])

    detector = vad.SileroSpeechDetector(Model())
    signal = np.array([0.01, -0.02, 0.03], dtype=np.float32)
    assert detector.max_probability(signal) == 0.75
    np.testing.assert_array_equal(received[0][:3], signal)
    np.testing.assert_array_equal(received[0][3:], np.zeros(509, dtype=np.float32))


@pytest.mark.parametrize("filename", [
    f"{language}-{index:02}.wav" for language in ("en", "ko") for index in range(10)
])
def test_all_fleurs_waveforms_reach_inference_without_an_amplitude_admission_gate(filename):
    engine = importlib.import_module("inference.whisper_streaming.engine")
    path = Path(__file__).resolve().parents[2] / "benchmarks/stt-ko-en-2026-10-28/audio" / filename
    with wave.open(str(path)) as sound:
        source = sound.readframes(sound.getnframes())
    segmenter = engine.UtteranceSegmenter()
    events = []
    for offset in range(0, len(source), 5120):
        events.extend(segmenter.feed(source[offset:offset + 5120]))
    events.extend(segmenter.finish())
    assert b"".join(audio for kind, audio in events if kind != "end") == source


def test_segmenter_preserves_quiet_speech_tails_and_fifteen_second_boundaries():
    engine = importlib.import_module("inference.whisper_streaming.engine")
    segmenter = engine.UtteranceSegmenter()
    source = pcm(496071, 2)
    events = segmenter.feed(source) + segmenter.finish()
    assert [kind for kind, _ in events].count("end") == 3
    assert b"".join(audio for kind, audio in events if kind != "end") == source
    assert segmenter.finish() == []
    assert segmenter.feed(pcm(71, 1)) == []
    assert segmenter.finish() == [("start", pcm(71, 1)), ("end", b"")]


def test_segmenter_uses_relative_silence_and_bounded_idle_preroll():
    engine = importlib.import_module("inference.whisper_streaming.engine")
    segmenter = engine.UtteranceSegmenter()
    for _ in range(50):
        assert segmenter.feed(pcm(16000, 0)) == []
        assert segmenter.buffered_bytes <= 6400
    source = pcm(16000, 8) + pcm(16000, 500) + pcm(32000, 20)
    events = segmenter.feed(source)
    assert not any(kind == "end" for kind, _ in events)
    events += segmenter.feed(pcm(12480, 8))
    assert not any(kind == "end" for kind, _ in events)
    events += segmenter.feed(pcm(320, 8))
    assert [kind for kind, _ in events].count("end") == 1
    delivered = b"".join(audio for kind, audio in events if kind != "end")
    assert delivered.endswith(source + pcm(12800, 8))
