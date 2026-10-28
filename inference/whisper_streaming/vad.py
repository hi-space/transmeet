"""Offline acoustic speech presence for the complete bounded utterance."""
import hashlib
from importlib import resources

SPEECH_THRESHOLD = 0.5
SAMPLE_RATE = 16000
MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"


class SileroSpeechDetector:
    """One CPU ONNX model, called under the runtime's shared inference lock."""

    def __init__(self, model):
        self.model = model

    @classmethod
    def load(cls):
        import torch

        threads = torch.get_num_threads()
        try:
            # Importing Silero changes Torch's global thread count. Keep that
            # side effect out of Whisper; its ONNX session uses one CPU thread.
            from silero_vad import load_silero_vad

            artifact = resources.files("silero_vad.data").joinpath("silero_vad.onnx")
            if hashlib.sha256(artifact.read_bytes()).hexdigest() != MODEL_SHA256:
                raise RuntimeError("The bundled Silero VAD model does not match the pinned artifact")
            model = load_silero_vad(onnx=True, opset_version=16)
        finally:
            torch.set_num_threads(threads)
        return cls(model)

    def max_probability(self, audio):
        import numpy as np
        import torch

        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim != 1 or len(audio) > 30 * SAMPLE_RATE:
            raise ValueError("VAD requires mono audio of at most 30 seconds")
        if not len(audio):
            return 0.0
        samples = torch.from_numpy(audio)
        if len(samples) < 512:
            samples = torch.nn.functional.pad(samples, (0, 512 - len(samples)))
        with torch.inference_mode():
            # The pinned wrapper resets recurrent state on every audio_forward
            # and zero-pads its last 512-sample frame. Never trim or amplify.
            probability = self.model.audio_forward(samples, SAMPLE_RATE).max().item()
        if not 0 <= probability <= 1:
            raise RuntimeError("Silero returned an invalid speech probability")
        return probability
