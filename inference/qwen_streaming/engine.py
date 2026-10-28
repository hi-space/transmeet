"""Bounded PCM endpointing, independent of Qwen's cumulative decoding state."""
import math
import struct
from collections import deque

from .endpoint_policy import should_finalize

SAMPLE_RATE = 16000
FRAME_SAMPLES = 320


def pcm_rms(pcm):
    if not pcm:
        return 0.0
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    return math.sqrt(sum(value * value for value in samples) / len(samples)) / 32768


class UtteranceSegmenter:
    """Retain nonzero audio; use relative energy only to choose boundaries.

    An absolute amplitude gate can erase quiet words before ASR sees them.
    Only exact digital silence is discarded while idle. Room tone therefore
    reaches the model too, in bounded utterances; this favors retaining speech
    over saving inference on uncertain background audio.
    """

    def __init__(self, threshold=0.001):
        self.threshold = threshold
        self._pending = bytearray()
        self._preroll = deque(maxlen=10)
        self._active = False
        self._speech_seen = False
        self._noise_levels = deque(maxlen=50)
        self._energy_threshold = threshold
        self._quiet_samples = 0
        self._utterance_samples = 0

    @property
    def buffered_bytes(self):
        return len(self._pending) + sum(len(frame) for frame in self._preroll)

    def _voiced(self, energy):
        if energy == 0:
            return False
        if not self._speech_seen:
            self._noise_levels.append(energy)
            levels = sorted(self._noise_levels)
            noise_floor = levels[len(levels) // 5]
            self._energy_threshold = max(1 / 32768, min(self.threshold, 2 * noise_floor))
            # A microphone's first frame may ramp up from zero. Do not mistake
            # the following room tone for speech before 200 ms of quiet-level
            # observations. Admission is unconditional, so no audio waits here.
            if len(levels) < 10 and energy < self.threshold:
                return False
        # Freeze the noise estimate after speech begins so a quieter following
        # phrase cannot raise its own silence threshold.
        return energy >= self._energy_threshold

    def feed(self, pcm):
        if len(pcm) % 2:
            raise ValueError("PCM16 audio must contain complete two-byte samples")
        self._pending.extend(pcm)
        frame_bytes = FRAME_SAMPLES * 2
        complete = len(self._pending) // frame_bytes * frame_bytes
        frames = bytes(self._pending[:complete])
        del self._pending[:complete]
        events = []
        for offset in range(0, complete, frame_bytes):
            frame = frames[offset:offset + frame_bytes]
            energy = pcm_rms(frame)
            voiced = self._voiced(energy)
            if not self._active:
                self._preroll.append(frame)
                if energy > 0:
                    initial = b"".join(self._preroll)
                    self._preroll.clear()
                    self._active = True
                    self._speech_seen = voiced
                    self._quiet_samples = 0
                    self._utterance_samples = len(initial) // 2
                    events.append(("start", initial))
                continue
            events.append(("audio", frame))
            self._utterance_samples += FRAME_SAMPLES
            self._speech_seen = self._speech_seen or voiced
            if voiced or (energy > 0 and not self._speech_seen):
                self._quiet_samples = 0
            else:
                self._quiet_samples += FRAME_SAMPLES
            if should_finalize(
                self._quiet_samples * 1000 // SAMPLE_RATE,
                self._utterance_samples * 1000 // SAMPLE_RATE,
            ):
                events.append(("end", b""))
                self._reset()
        return events

    def _reset(self):
        self._active = False
        self._speech_seen = False
        self._quiet_samples = 0
        self._utterance_samples = 0
        self._preroll.clear()

    def finish(self):
        tail = bytes(self._pending)
        self._pending.clear()
        events = []
        if self._active:
            if tail:
                events.append(("audio", tail))
            events.append(("end", b""))
        else:
            pending = b"".join(self._preroll) + tail
            if any(pending):
                events.extend((("start", pending), ("end", b"")))
        self._reset()
        return events
