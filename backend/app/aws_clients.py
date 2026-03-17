import re
import struct
import time
import random
import string
from contextlib import asynccontextmanager

import aioboto3

from .config import config

_session = aioboto3.Session()


@asynccontextmanager
async def sagemaker_client():
    async with _session.client("sagemaker-runtime", region_name=config.REGION) as client:
        yield client


@asynccontextmanager
async def bedrock_client():
    async with _session.client("bedrock-runtime", region_name=config.REGION) as client:
        yield client



@asynccontextmanager
async def dynamodb_client():
    async with _session.client("dynamodb", region_name=config.REGION) as client:
        yield client


def normalize_language(lang: str) -> str:
    l = lang.lower()
    if l in ("ko", "korean", "kor"):
        return "ko"
    return "en"


SILENCE_RMS_THRESHOLD = 0.01


def compute_rms(wav_bytes: bytes) -> float:
    """Compute normalized RMS energy of a WAV buffer (skips 44-byte header)."""
    pcm = wav_bytes[44:]
    if len(pcm) < 2:
        return 0.0
    num_samples = len(pcm) // 2
    samples = struct.unpack_from(f"<{num_samples}h", pcm)
    rms = (sum(s * s for s in samples) / num_samples) ** 0.5
    return min(rms / 32768.0, 1.0)


_HALLUCINATION_PATTERNS = [
    re.compile(r"^(uh\.?|um\.?|hmm+\.?)$", re.IGNORECASE),
    re.compile(r"^thanks\s+for\s+watching\.?$", re.IGNORECASE),
    re.compile(r"^(please\s+)?subscribe\.?$", re.IGNORECASE),
    re.compile(r"^(like\s+and\s+subscribe\.?)$", re.IGNORECASE),
    re.compile(r"^\[.*\]$"),
    re.compile(r"^\(.*\)$"),
    re.compile(r"^thank\s+you[\.,!]?$", re.IGNORECASE),
    re.compile(r"^thank\s+you\s+(so\s+much|very\s+much)[\.,!]?$", re.IGNORECASE),
    re.compile(r"^you[\.,!]?$", re.IGNORECASE),
    re.compile(r"^(ok|okay)[\.,!]?$", re.IGNORECASE),
    re.compile(r"^bye[\.,?!]*$", re.IGNORECASE),
    re.compile(r"^(good\s+)?bye[\s\-]?bye[\.,?!]*$", re.IGNORECASE),
    re.compile(r"^see\s+you[\.,!]?$", re.IGNORECASE),
    re.compile(r"^(hello|hi)[\.,!]?$", re.IGNORECASE),
    re.compile(r"^\.{1,3}$"),
]


def is_hallucination(text: str) -> bool:
    return any(p.match(text) for p in _HALLUCINATION_PATTERNS)


def generate_message_id() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
    return f"{int(time.time() * 1000)}-{suffix}"
