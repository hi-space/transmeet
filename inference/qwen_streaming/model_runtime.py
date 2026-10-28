"""Official Qwen streaming API with isolated state and serialized GPU calls."""
import asyncio
import logging
import os
import threading
from collections import deque

logger = logging.getLogger(__name__)
SAMPLE_RATE = 16000
MAX_UTTERANCE_BYTES = 15 * SAMPLE_RATE * 2
MAX_NEW_TOKENS = 512
CANCEL_TIMEOUT = 5
LANGUAGES = {"ko-KR": "Korean", "en-US": "English", "auto": None}


def finish_without_unsafe_prefix(model, state):
    """Flush using the official API, without its unsafe tail prefix rollback.

    qwen-asr 0.0.6's chunk path checks for U+FFFD after byte-BPE rollback;
    its finalizer instead keeps at least one token and can split Korean UTF-8.
    A final tail therefore uses no retained text prefix. The 512-token budget
    covers re-decoding the entire bounded 15-second utterance.
    """
    previous = state.unfixed_chunk_num
    state.unfixed_chunk_num = state.chunk_id + 1
    try:
        return model.finish_streaming_transcribe(state)
    finally:
        state.unfixed_chunk_num = previous


class ModelRuntime:
    def __init__(self, model, max_workers=4, max_queued_bytes=128000):
        self.model = model
        self.max_queued_bytes = max_queued_bytes
        self._workers = threading.BoundedSemaphore(max_workers)
        self._engine_lock = threading.Lock()

    @classmethod
    def load(cls):
        import numpy as np
        import torch
        from qwen_asr import Qwen3ASRModel

        if not torch.cuda.is_available():
            raise RuntimeError("The Qwen speech container requires an available CUDA GPU")
        torch.set_num_threads(2)
        model_dir = os.environ.get("MODEL_DIR", "/opt/ml/model")
        model = Qwen3ASRModel.LLM(
            model=model_dir,
            dtype="bfloat16",
            gpu_memory_utilization=0.78,
            max_model_len=8192,
            max_num_seqs=1,
            max_new_tokens=MAX_NEW_TOKENS,
            max_inference_batch_size=1,
            enforce_eager=True,
            disable_log_stats=True,
        )
        warm = model.init_streaming_state(language="English", chunk_size_sec=1.0)
        model.streaming_transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), warm)
        finish_without_unsafe_prefix(model, warm)
        logger.info("Qwen streaming ready: model=%s gpu=%s", model_dir, torch.cuda.get_device_name())
        return cls(model)

    def start_utterance(self, *, language, chunk_ms, on_partial):
        if language not in LANGUAGES or type(chunk_ms) is not int or chunk_ms not in (1000, 2000):
            raise ValueError("Qwen requires ko-KR, en-US, or auto and 1000/2000 ms model chunks")
        if not self._workers.acquire(blocking=False):
            raise RuntimeError("The Qwen speech decoder is at capacity; retry shortly")
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
        self._audio = deque()
        self._queued_bytes = 0
        self._total_bytes = 0
        self._condition = threading.Condition()
        self._cancelled = False
        self._ended = False
        self.task = asyncio.create_task(asyncio.to_thread(self._run))
        self.task.add_done_callback(self._observe_result)

    @staticmethod
    def _observe_result(task):
        # A disconnected client may never await its failed worker again.
        if not task.cancelled():
            task.exception()

    def feed(self, pcm):
        if len(pcm) % 2:
            raise ValueError("PCM16 audio must contain complete two-byte samples")
        with self._condition:
            if self._ended or self._cancelled:
                raise RuntimeError("Audio arrived after the utterance ended")
            if self.task.done():
                self.task.result()
                raise RuntimeError("The Qwen decoder ended before all audio arrived")
            if self._total_bytes + len(pcm) > MAX_UTTERANCE_BYTES:
                raise ValueError("A Qwen utterance cannot exceed 15 seconds")
            if self._queued_bytes + len(pcm) > self.runtime.max_queued_bytes or len(self._audio) >= 256:
                raise RuntimeError("The Qwen speech decoder cannot keep up with incoming audio")
            if pcm:
                self._audio.append(bytes(pcm))
                self._queued_bytes += len(pcm)
                self._total_bytes += len(pcm)
                self._condition.notify()

    async def finish(self):
        with self._condition:
            self._ended = True
            self._condition.notify()
        return await asyncio.shield(self.task)

    async def cancel(self):
        with self._condition:
            self._cancelled = True
            self._audio.clear()
            self._queued_bytes = 0
            self._condition.notify()
        try:
            await asyncio.wait_for(asyncio.shield(self.task), timeout=CANCEL_TIMEOUT)
        except (Exception, asyncio.CancelledError):
            pass

    def _next_audio(self):
        with self._condition:
            while not self._audio and not self._ended and not self._cancelled:
                self._condition.wait()
            if self._cancelled:
                raise RuntimeError("Qwen speech decoding cancelled")
            if not self._audio:
                return None
            pcm = self._audio.popleft()
            self._queued_bytes -= len(pcm)
            return pcm

    def _check_cancelled(self):
        with self._condition:
            if self._cancelled:
                raise RuntimeError("Qwen speech decoding cancelled")

    def _run(self):
        try:
            return self._generate()
        finally:
            # Neither socket cancellation nor timeout can stop a CUDA call.
            # Release only when this actual synchronous worker exits.
            self.runtime._workers.release()

    def _generate(self):
        import numpy as np

        with self.runtime._engine_lock:
            self._check_cancelled()
            state = self.runtime.model.init_streaming_state(
                language=LANGUAGES[self.language], chunk_size_sec=self.chunk_ms / 1000,
                unfixed_chunk_num=2, unfixed_token_num=5,
            )
        pending = bytearray()
        chunk_bytes = self.chunk_ms * SAMPLE_RATE * 2 // 1000
        previous = None

        def decode(pcm):
            audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
            with self.runtime._engine_lock:
                self._check_cancelled()
                self.runtime.model.streaming_transcribe(audio, state)
            self._check_cancelled()

        while True:
            pcm = self._next_audio()
            if pcm is None:
                break
            pending.extend(pcm)
            while len(pending) >= chunk_bytes:
                chunk = bytes(pending[:chunk_bytes])
                del pending[:chunk_bytes]
                decode(chunk)
                result = (state.text.strip(), state.language)
                if result[0] and "\ufffd" not in result[0] and result != previous:
                    self.on_partial(*result)
                    previous = result
        if pending:
            decode(bytes(pending))
        with self.runtime._engine_lock:
            self._check_cancelled()
            finish_without_unsafe_prefix(self.runtime.model, state)
        self._check_cancelled()
        if "\ufffd" in state.text:
            raise RuntimeError("Qwen returned an incomplete Unicode transcript")
        return state.text.strip(), state.language
