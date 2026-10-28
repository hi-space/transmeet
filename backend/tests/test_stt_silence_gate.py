"""Long STT chunks retain speech accepted by the original two-second gate."""
import base64
import io
import math
import struct
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import aws_clients, stt, ws_handler


SAMPLE_RATE = 16000


def tone(seconds, amplitude=640):
    """A voiced signal surrogate with RMS just above the existing threshold."""
    samples = (
        int(amplitude * math.sin(2 * math.pi * 220 * index / SAMPLE_RATE))
        for index in range(round(seconds * SAMPLE_RATE))
    )
    return b"".join(struct.pack("<h", sample) for sample in samples)


def silence(seconds):
    return b"\x00\x00" * round(seconds * SAMPLE_RATE)


def wav(pcm):
    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(pcm)
    return out.getvalue()


@pytest.mark.parametrize("leading,trailing", [(0, 4), (2, 2), (4, 0)])
def test_speech_survives_leading_and_trailing_pauses(leading, trailing):
    speech = tone(2)
    audio = wav(silence(leading) + speech + silence(trailing))
    original_rms = aws_clients.compute_rms(wav(speech))

    assert original_rms > aws_clients.SILENCE_RMS_THRESHOLD
    assert aws_clients.compute_rms(audio) < aws_clients.SILENCE_RMS_THRESHOLD
    assert aws_clients.compute_peak_rms(audio) == pytest.approx(original_rms)


@pytest.mark.parametrize("seconds", [2, 4, 6, 8, 10])
def test_selected_chunk_duration_keeps_original_two_second_acceptance(seconds):
    speech = tone(2)
    original_rms = aws_clients.compute_rms(wav(speech))

    assert aws_clients.compute_peak_rms(wav(speech + silence(seconds - 2))) == pytest.approx(
        original_rms
    )


def test_aggregate_rms_still_includes_silence():
    speech = tone(2)
    original_rms = aws_clients.compute_rms(wav(speech))

    assert aws_clients.compute_rms(wav(speech + silence(4))) == pytest.approx(
        original_rms / math.sqrt(3)
    )


@pytest.mark.parametrize("amplitude", [0, 160], ids=["silent", "quiet"])
def test_silent_and_quiet_chunks_stay_below_threshold(amplitude):
    assert aws_clients.compute_peak_rms(wav(tone(6, amplitude))) < aws_clients.SILENCE_RMS_THRESHOLD


@pytest.mark.parametrize("amplitude,accepted", [(640, True), (160, False), (0, False)])
def test_short_final_window_uses_its_actual_sample_count(amplitude, accepted):
    tail = tone(0.05, amplitude=amplitude)
    audio = wav(silence(6) + tail)
    peak = aws_clients.compute_peak_rms(audio)

    assert peak == pytest.approx(aws_clients.compute_rms(wav(tail)))
    assert (peak >= aws_clients.SILENCE_RMS_THRESHOLD) is accepted


def test_brief_noise_does_not_become_speech_through_tiny_windows():
    # A 1ms impulse must still be averaged over the original two-second window.
    impulse = struct.pack("<h", 3000) * 16
    original_chunk = impulse + silence(2 - 0.001)
    audio = wav(original_chunk + silence(4))

    assert aws_clients.compute_peak_rms(audio) == pytest.approx(
        aws_clients.compute_rms(wav(original_chunk))
    )
    assert aws_clients.compute_peak_rms(audio) < aws_clients.SILENCE_RMS_THRESHOLD


@pytest.mark.parametrize(
    "audio", [b"", b"\x00" * 44, b"\x00" * 45], ids=["empty", "header_only", "partial_sample"],
)
def test_empty_or_incomplete_pcm_has_zero_energy(audio):
    assert aws_clients.compute_rms(audio) == 0
    assert aws_clients.compute_peak_rms(audio) == 0


@pytest.fixture
def recorded_requests(monkeypatch):
    requests = []

    async def authenticate(ws):
        return "offline-user", ""

    async def transcribe(provider, audio, language):
        requests.append((provider, audio, language))
        return stt.STTResult("회의를 시작합니다.", "ko")

    async def process(*args):
        pass

    monkeypatch.setattr(ws_handler, "_authenticate", authenticate)
    monkeypatch.setattr(ws_handler, "transcribe_audio", transcribe)
    monkeypatch.setattr(ws_handler, "_process_segment", process)
    monkeypatch.setattr(ws_handler, "_start_flush", lambda *args: None)
    monkeypatch.setattr(stt.config, "QWEN_ASR_ENDPOINT", "offline-qwen")
    monkeypatch.setattr(stt.config, "NEMOTRON_ASR_ENDPOINT", "offline-nemotron")
    monkeypatch.setattr(stt.config, "QWEN_ASR_INFERENCE_COMPONENT", "")
    monkeypatch.setattr(stt.config, "NEMOTRON_ASR_INFERENCE_COMPONENT", "")
    return requests


def send_recording(provider, audio):
    app = FastAPI()
    app.include_router(ws_handler.router)
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"action": "startRecording", "sttProvider": provider, "sourceLang": "ko"})
        ws.send_json({"action": "sendAudio", "audioData": base64.b64encode(audio).decode()})
        ws.send_json({"action": "stopRecording"})
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["type"] == "pong"


@pytest.mark.parametrize("provider", ["whisper", "qwen3-asr", "nemotron"])
def test_gate_forwards_entire_long_chunk_when_it_contains_speech(recorded_requests, provider):
    audio = wav(tone(2) + silence(4))
    assert aws_clients.compute_rms(audio) < aws_clients.SILENCE_RMS_THRESHOLD

    send_recording(provider, audio)

    assert len(recorded_requests) == 1
    assert recorded_requests == [(provider, audio, "ko")]


@pytest.mark.parametrize("amplitude", [0, 160], ids=["silent", "quiet"])
def test_gate_does_not_invoke_stt_for_silent_or_quiet_chunks(recorded_requests, amplitude):
    send_recording("whisper", wav(tone(6, amplitude)))

    assert recorded_requests == []
