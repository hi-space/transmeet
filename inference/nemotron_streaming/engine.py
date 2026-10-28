"""Incremental PCM, endpointing, and isolated model execution.

No ML imports are needed to test audio ordering and utterance boundaries.
"""
import copy
import math
import re
import struct
from collections import deque
from dataclasses import dataclass
from typing import Iterator

from .endpoint_policy import should_finalize

SAMPLE_RATE = 16000
FRAME_SAMPLES = 320  # 20 ms energy windows, independent of network chunk size.


@dataclass(frozen=True)
class AudioWindow:
    pcm: bytes
    first: bool


class PcmWindows:
    """Produce fixed-size STFT windows without reprocessing a growing recording."""

    def __init__(self, *, first_samples, next_samples, first_advance, advance, left_context):
        self.first_samples = first_samples
        self.next_samples = next_samples
        self.first_advance = first_advance
        self.advance = advance
        self.left_context = left_context
        self._buffer = bytearray()
        self._first = True
        self._closed = False

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, pcm: bytes) -> Iterator[AudioWindow]:
        if self._closed:
            raise ValueError("Audio was received after this utterance ended")
        if len(pcm) % 2:
            raise ValueError("PCM16 audio must contain complete two-byte samples")
        self._buffer.extend(pcm)
        while len(self._buffer) >= 2 * self._needed_samples():
            yield self._take()

    def _needed_samples(self):
        return self.first_samples if self._first else self.next_samples

    def _take(self):
        needed = self._needed_samples() * 2
        frame = AudioWindow(bytes(self._buffer[:needed]), self._first)
        advance = self.first_advance if self._first else self.advance
        del self._buffer[:advance * 2]
        self._first = False
        return frame

    def finish(self) -> Iterator[AudioWindow]:
        if self._closed:
            return
        self._closed = True
        # Retained STFT overlap is not new audio. A new mel-frame centre must
        # fall inside real input before a padded final window is warranted.
        remaining = len(self._buffer) // 2
        if remaining > (0 if self._first else self.left_context):
            self._buffer.extend(b"\0" * (2 * self._needed_samples() - len(self._buffer)))
            yield self._take()
        self._buffer.clear()


def pcm_rms(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    values = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    return math.sqrt(sum(value * value for value in values) / len(values)) / 32768


class UtteranceSegmenter:
    """Start on speech, preserve pre-roll, and include silence in active streams."""

    def __init__(self, threshold: float = 0.01):
        self.threshold = threshold
        self._pending = bytearray()
        self._preroll = deque(maxlen=10)  # 200 ms
        self._active = False
        self._voiced_frames = 0
        self._quiet_samples = 0
        self._utterance_samples = 0

    @property
    def buffered_bytes(self):
        return len(self._pending) + sum(len(frame) for frame in self._preroll)

    def feed(self, pcm: bytes) -> list[tuple[str, bytes]]:
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
            voiced = pcm_rms(frame) >= self.threshold
            if not self._active:
                self._preroll.append(frame)
                self._voiced_frames = self._voiced_frames + 1 if voiced else 0
                if self._voiced_frames >= 2:
                    initial = b"".join(self._preroll)
                    self._preroll.clear()
                    self._active = True
                    self._quiet_samples = 0
                    self._utterance_samples = len(initial) // 2
                    events.append(("start", initial))
                continue
            events.append(("audio", frame))
            self._utterance_samples += FRAME_SAMPLES
            self._quiet_samples = 0 if voiced else self._quiet_samples + FRAME_SAMPLES
            if should_finalize(
                self._quiet_samples * 1000 // SAMPLE_RATE,
                self._utterance_samples * 1000 // SAMPLE_RATE,
            ):
                events.append(("end", b""))
                self._reset()
        return events

    def _reset(self):
        self._active = False
        self._voiced_frames = 0
        self._quiet_samples = 0
        self._utterance_samples = 0
        self._preroll.clear()

    def finish(self) -> list[tuple[str, bytes]]:
        tail = bytes(self._pending)
        self._pending.clear()
        events = []
        if self._active:
            if tail:
                events.append(("audio", tail))
            events.append(("end", b""))
        else:
            pending = b"".join(self._preroll) + tail
            # Explicit stop can happen before the two-frame start debounce.
            if pending and (self._voiced_frames or (tail and pcm_rms(tail) >= self.threshold)):
                events.extend((("start", pending), ("end", b"")))
        self._reset()
        return events


_LANGUAGE_TAG = re.compile(r"\s*<([a-z]{2,3}-[A-Za-z]{2,4})>\s*$")


def clean_transcript(text, language, special_tokens):
    language = "" if language == "auto" else language
    # RNNT can emit blanks after the language tag. Preserve locale tokens until
    # their metadata has been extracted, including tokenizers that mark them special.
    for token in special_tokens:
        if not _LANGUAGE_TAG.fullmatch(token):
            text = text.replace(token, "")
    match = _LANGUAGE_TAG.search(text)
    if match:
        language = match[1]
        text = text[:match.start()]
    return text.strip(), language


def isolate_model(model):
    """Share immutable GPU weights while isolating Transformers generation state.

    Nemotron's generation mixins assign _prompt_ids, _streaming and
    _encoder_finished to the model instance. Concurrent generate() calls on the
    same instance would corrupt each other's language and decoder lifecycle.
    """
    handle = copy.copy(model)
    handle.generation_config = copy.deepcopy(model.generation_config)
    return handle
