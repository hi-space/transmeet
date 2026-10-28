"""Offline Whisper with bounded cumulative audio and serialized GPU calls."""
import asyncio
import copy
import logging
import os
import threading
import unicodedata

from .vad import SPEECH_THRESHOLD, SileroSpeechDetector

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
SAMPLE_RATE = 16000
MAX_UTTERANCE_BYTES = 15 * SAMPLE_RATE * 2
MAX_BATCH_BYTES = 30 * SAMPLE_RATE * 2
MAX_NEW_TOKENS = 440
CANCEL_TIMEOUT = 5
LANGUAGES = {"ko-KR": "ko", "en-US": "en", "auto": None}
LANGUAGE_NAMES = {"ko-KR": "Korean", "en-US": "English", "auto": None}


def observe_result(task):
    """Disconnected clients may no longer be awaiting a failed worker."""
    if not task.cancelled():
        task.exception()


class WhisperDecoder:
    """The Transformers 4.39.3 shortform generation boundary.

    Silero detects acoustic speech before text generation. Whisper's automatic
    language detection reuses generation's encoder output. Every call owns its
    generation configuration and preserves the complete accepted waveform.
    """

    def __init__(self, model, processor, speech_detector):
        self.model = model
        self.processor = processor
        self.speech_detector = speech_detector

    def decode(self, pcm, language):
        import numpy as np
        import torch

        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        probability = self.speech_detector.max_probability(audio)
        skip = probability < SPEECH_THRESHOLD
        logger.info(
            "Whisper speech gate audio_seconds=%.3f vad_max_probability=%.6f skipped=%s",
            len(audio) / SAMPLE_RATE, probability, skip,
        )
        if skip:
            return "", LANGUAGE_NAMES[language] or ""
        parameter = next(self.model.parameters())
        with torch.inference_mode():
            features = self.processor(
                audio, sampling_rate=SAMPLE_RATE, return_tensors="pt",
            ).input_features.to(device=parameter.device, dtype=parameter.dtype)
            config = copy.deepcopy(self.model.generation_config)
            # Saved settings must not force the previous/default language or
            # translation task when a subsequent session requests auto.
            config.language = None
            config.task = "transcribe"
            config.forced_decoder_ids = None
            encoder_outputs = self.model.get_encoder()(features, return_dict=True)
            language_tokens = {value: key for key, value in config.lang_to_id.items()}
            selected = LANGUAGES[language]
            if selected is None:
                language_id = self.model.detect_language(
                    encoder_outputs=encoder_outputs, generation_config=config,
                )[0].item()
                selected = language_tokens[language_id][2:-2]
            predicted = self.model.generate(
                features,
                encoder_outputs=encoder_outputs,
                generation_config=config,
                language=selected,
                task="transcribe",
                return_timestamps=False,
                return_dict_in_generate=False,
                max_new_tokens=min(MAX_NEW_TOKENS, self.model.config.max_target_positions - 4),
                num_beams=1,
                do_sample=False,
            )
        ids = predicted[0].tolist()
        eos_ids = config.eos_token_id
        if isinstance(eos_ids, int):
            eos_ids = [eos_ids]
        if not ids or ids[-1] not in (eos_ids or []):
            raise RuntimeError("Whisper transcription exceeded its token limit")
        if len(ids) < 2 or ids[1] not in language_tokens:
            raise RuntimeError("Whisper did not return a detected language token")
        detected = self._language_name(language_tokens[ids[1]][2:-2])
        text = self.processor.batch_decode(predicted, skip_special_tokens=True)[0].strip()
        if "\ufffd" in text:
            raise RuntimeError("Whisper returned an incomplete Unicode transcript")
        return unicodedata.normalize("NFC", text), detected

    @staticmethod
    def _language_name(code):
        if code in ("ko", "en"):
            return {"ko": "Korean", "en": "English"}[code]
        from transformers.models.whisper.tokenization_whisper import LANGUAGES as names
        return names.get(code, code).title()


class ModelRuntime:
    def __init__(self, decoder, max_workers=4, max_queued_bytes=128000):
        if max_workers < 1 or max_queued_bytes < 1:
            raise ValueError("Whisper worker and queue limits must be positive")
        self.decoder = decoder
        self.max_queued_bytes = max_queued_bytes
        self._workers = threading.BoundedSemaphore(max_workers)
        self._engine_lock = threading.Lock()

    @classmethod
    def load(cls):
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        if not torch.cuda.is_available():
            raise RuntimeError("The Whisper speech container requires an available CUDA GPU")
        torch.set_num_threads(2)
        speech_detector = SileroSpeechDetector.load()
        model_dir = os.environ.get("MODEL_DIR", "/opt/ml/model")
        model = WhisperForConditionalGeneration.from_pretrained(
            model_dir, torch_dtype=torch.float16, local_files_only=True,
        ).to("cuda")
        model.config.forced_decoder_ids = None
        model.eval()
        processor = WhisperProcessor.from_pretrained(model_dir, local_files_only=True)
        decoder = WhisperDecoder(model, processor, speech_detector)
        decoder.decode(b"\0" * (SAMPLE_RATE * 2), "en-US")
        logger.info("Whisper ready: model=%s gpu=%s", model_dir, torch.cuda.get_device_name())
        return cls(decoder)

    def _acquire(self):
        if not self._workers.acquire(blocking=False):
            raise RuntimeError("The Whisper speech decoder is at capacity; retry shortly")

    def start_utterance(self, *, language, chunk_ms, on_partial):
        if language not in LANGUAGES or type(chunk_ms) is not int or chunk_ms not in (1000, 2000):
            raise ValueError("Whisper requires ko-KR, en-US, or auto and 1000/2000 ms model chunks")
        self._acquire()
        try:
            return StreamingUtterance(self, language, chunk_ms, on_partial)
        except BaseException:
            self._workers.release()
            raise

    async def transcribe(self, pcm, *, language):
        """Batch audio has Whisper's native 30-second bound and shares permits."""
        if language not in LANGUAGES:
            raise ValueError("Whisper language must be ko-KR, en-US, or auto")
        if not pcm or len(pcm) % 2:
            raise ValueError("Batch audio must contain complete PCM16 samples")
        if len(pcm) > MAX_BATCH_BYTES:
            raise ValueError("A Whisper batch request cannot exceed 30 seconds")
        self._acquire()
        cancelled = threading.Event()

        def run():
            try:
                with self._engine_lock:
                    if cancelled.is_set():
                        raise RuntimeError("Whisper batch decoding cancelled")
                    return self.decoder.decode(pcm, language)
            finally:
                self._workers.release()

        task = asyncio.create_task(asyncio.to_thread(run))
        task.add_done_callback(observe_result)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()
            raise


class StreamingUtterance:
    def __init__(self, runtime, language, chunk_ms, on_partial):
        self.runtime = runtime
        self.language = language
        self.on_partial = on_partial
        self._chunk_bytes = chunk_ms * SAMPLE_RATE * 2 // 1000
        self._audio = bytearray()
        self._inflight_bytes = 0
        self._decoded_bytes = 0
        self._result = ("", LANGUAGE_NAMES[language])
        self._condition = threading.Condition()
        self._cancelled = False
        self._ended = False
        self.task = asyncio.create_task(asyncio.to_thread(self._run))
        self.task.add_done_callback(observe_result)

    def feed(self, pcm):
        if len(pcm) % 2:
            raise ValueError("PCM16 audio must contain complete two-byte samples")
        with self._condition:
            if self._ended or self._cancelled:
                raise RuntimeError("Audio arrived after the utterance ended")
            if self.task.done():
                self.task.result()
                raise RuntimeError("The Whisper decoder ended before all audio arrived")
            if len(self._audio) + len(pcm) > MAX_UTTERANCE_BYTES:
                raise ValueError("A Whisper utterance cannot exceed 15 seconds")
            queued = len(self._audio) - self._inflight_bytes
            if queued + len(pcm) > self.runtime.max_queued_bytes:
                raise RuntimeError("The Whisper speech decoder cannot keep up with incoming audio")
            self._audio.extend(pcm)
            self._condition.notify()

    async def finish(self):
        with self._condition:
            self._ended = True
            self._condition.notify()
        # Socket timeouts must not cancel the actual thread and free its slot.
        return await asyncio.shield(self.task)

    async def cancel(self):
        with self._condition:
            self._cancelled = True
            self._condition.notify()
        try:
            await asyncio.wait_for(asyncio.shield(self.task), timeout=CANCEL_TIMEOUT)
        except (Exception, asyncio.CancelledError):
            pass

    def _check_cancelled(self):
        if self._cancelled:
            raise RuntimeError("Whisper speech decoding cancelled")

    def _run(self):
        try:
            return self._generate()
        finally:
            with self._condition:
                self._audio.clear()
            # CUDA calls cannot be stopped by asyncio cancellation. The actual
            # synchronous worker, and only that worker, releases the permit.
            self.runtime._workers.release()

    def _generate(self):
        previous = None
        while True:
            with self._condition:
                while (
                    not self._ended and not self._cancelled
                    and len(self._audio) - self._decoded_bytes < self._chunk_bytes
                ):
                    self._condition.wait()
                self._check_cancelled()
                if self._ended and len(self._audio) == self._decoded_bytes:
                    return self._result
            with self.runtime._engine_lock:
                with self._condition:
                    self._check_cancelled()
                    # Snapshot only after acquiring the GPU. Audio arriving
                    # during another session's inference is coalesced too.
                    audio = bytes(self._audio)
                    self._inflight_bytes = len(audio)
                result = self.runtime.decoder.decode(audio, self.language)
            with self._condition:
                self._check_cancelled()
                self._decoded_bytes = len(audio)
                self._result = result
                if self._ended and len(self._audio) == self._decoded_bytes:
                    return result
            if result[0] and result != previous:
                self.on_partial(*result)
                previous = result
