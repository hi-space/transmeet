"""Streaming Transformers adapter; weights load once, execution state is per utterance."""
import asyncio
import copy
import logging
import os
import queue
import threading

from .engine import PcmWindows, clean_transcript, isolate_model

logger = logging.getLogger(__name__)
CANCEL_TIMEOUT = 5


class ModelRuntime:
    def __init__(self, model, processor, max_workers=4):
        self.model = model
        self.processor = processor
        self._workers = threading.BoundedSemaphore(max_workers)

    @classmethod
    def load(cls):
        import numpy as np
        import torch
        from transformers import AutoModelForRNNT, AutoProcessor

        if not torch.cuda.is_available():
            raise RuntimeError("The speech container requires an available CUDA GPU")
        torch.set_num_threads(2)
        model_dir = os.environ.get("MODEL_DIR", "/opt/ml/model")
        processor = AutoProcessor.from_pretrained(model_dir, local_files_only=True)
        model = AutoModelForRNNT.from_pretrained(model_dir, local_files_only=True)
        model.to("cuda")
        model.eval()
        for right in (3, 6):
            if right not in processor.supported_num_lookahead_tokens:
                raise RuntimeError(f"The deployed model does not support lookahead {right}")
        warm = processor(np.zeros(16000, dtype=np.float32), sampling_rate=16000, language="en-US")
        warm = warm.to(model.device, dtype=model.dtype)
        with torch.inference_mode():
            isolate_model(model).generate(**warm, max_new_tokens=512)
        logger.info("Nemotron streaming ready: model=%s cuda=%s", model_dir, torch.cuda.get_device_name())
        return cls(model, processor)

    def start_utterance(self, *, language, chunk_ms, on_partial):
        if not self._workers.acquire(blocking=False):
            raise RuntimeError("The speech decoder is at capacity; retry shortly")
        try:
            return StreamingUtterance(self, language, chunk_ms, on_partial)
        except BaseException:
            self._workers.release()
            raise


class StreamingUtterance:
    def __init__(self, runtime, language, chunk_ms, on_partial):
        self.runtime = runtime
        self.language = language
        self.chunk_ms = chunk_ms
        self.on_partial = on_partial
        self.audio = queue.Queue(maxsize=256)  # 5.12 s at the segmenter's 20 ms frames.
        self.cancelled = threading.Event()
        self.ended = False
        self.drained = False
        self.task = asyncio.create_task(asyncio.to_thread(self._run))

    def _run(self):
        try:
            return self._generate()
        finally:
            # A socket timeout cannot terminate a CUDA call. Hold this permit
            # until the real worker exits, even after the caller disconnects.
            self.runtime._workers.release()

    def feed(self, pcm):
        if self.ended or self.cancelled.is_set():
            raise RuntimeError("Audio arrived after the utterance ended")
        if self.task.done():
            # Propagate an inference failure immediately instead of filling a dead queue.
            self.task.result()
            raise RuntimeError("The decoder ended before all audio arrived")
        try:
            self.audio.put_nowait(pcm)
        except queue.Full as exc:
            raise RuntimeError("The speech decoder cannot keep up with incoming audio") from exc

    async def finish(self):
        if not self.ended:
            self.ended = True
            await asyncio.to_thread(self.audio.put, None, True, 5)
        return await asyncio.shield(self.task)

    async def cancel(self):
        self.cancelled.set()
        while True:
            try:
                self.audio.get_nowait()
            except queue.Empty:
                break
        self.audio.put_nowait(None)
        try:
            await asyncio.wait_for(asyncio.shield(self.task), timeout=CANCEL_TIMEOUT)
        except (Exception, asyncio.CancelledError):
            pass

    def _windows(self, processor):
        extractor = processor.feature_extractor
        windows = PcmWindows(
            first_samples=processor.num_samples_first_audio_chunk,
            next_samples=processor.num_samples_per_audio_chunk,
            first_advance=(
                processor.num_mel_frames_first_audio_chunk * extractor.hop_length - extractor.n_fft // 2
            ),
            advance=processor.num_mel_frames_per_audio_chunk * extractor.hop_length,
            left_context=extractor.n_fft // 2,
        )
        while not self.cancelled.is_set():
            try:
                pcm = self.audio.get(timeout=0.5)
            except queue.Empty:
                continue
            if pcm is None:
                yield from windows.finish()
                self.drained = True
                return
            yield from windows.feed(pcm)
        raise RuntimeError("Speech decoding cancelled")

    def _generate(self):
        import numpy as np
        import torch

        model = isolate_model(self.runtime.model)
        processor = copy.copy(self.runtime.processor)
        right = {320: 3, 560: 6}[self.chunk_ms]
        processor.set_num_lookahead_tokens(right)
        windows = self._windows(processor)

        def process(window):
            audio = np.frombuffer(window.pcm, dtype="<i2").astype(np.float32) / 32768.0
            inputs = processor(
                audio, sampling_rate=16000, language=self.language,
                is_streaming=True, is_first_audio_chunk=window.first, return_tensors="pt",
            )
            return inputs.to(model.device, dtype=model.dtype)

        first_window = next(windows, None)
        if first_window is None:
            return "", "" if self.language == "auto" else self.language
        first = process(first_window)

        def features():
            yield first.input_features[:, :processor.num_mel_frames_first_audio_chunk, :]
            for window in windows:
                inputs = process(window)
                yield inputs.input_features[:, :processor.num_mel_frames_per_audio_chunk, :]

        special_tokens = list(processor.tokenizer.all_special_tokens)
        parent = self

        class TokenStream:
            def __init__(self):
                self.ids = []
                self.last = None

            def put(self, value):
                if parent.cancelled.is_set():
                    raise RuntimeError("Speech decoding cancelled")
                self.ids.extend(value.detach().cpu().reshape(-1).tolist())
                raw = processor.decode(self.ids, skip_special_tokens=False)
                text, language = clean_transcript(raw, parent.language, special_tokens)
                if "\ufffd" not in text and text and (text, language) != self.last:
                    self.last = (text, language)
                    parent.on_partial(text, language)

            def end(self):
                pass

        arguments = dict(first)
        arguments["input_features"] = features()
        with torch.inference_mode():
            output = model.generate(
                **arguments, streamer=TokenStream(), return_dict_in_generate=True,
                max_new_tokens=4096,
            )
        if not self.drained:
            raise RuntimeError("The decoder stopped before consuming the complete utterance")
        raw = processor.decode(output.sequences[0].detach().cpu().tolist(), skip_special_tokens=False)
        return clean_transcript(raw, self.language, special_tokens)
