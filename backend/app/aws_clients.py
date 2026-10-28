import re
import struct
import time
import random
import string
from contextlib import asynccontextmanager

import aiobotocore.session
from aiobotocore.config import AioConfig

from .config import config

_session = aiobotocore.session.get_session()


@asynccontextmanager
async def sagemaker_client():
    async with _session.create_client("sagemaker-runtime", region_name=config.REGION) as client:
        yield client


@asynccontextmanager
async def sagemaker_control_client():
    async with _session.create_client(
        "sagemaker", region_name=config.REGION,
        config=AioConfig(connect_timeout=5, read_timeout=15, retries={"max_attempts": 1}),
    ) as client:
        yield client


@asynccontextmanager
async def stt_metrics_client():
    async with _session.create_client(
        "cloudwatch", region_name=config.REGION,
        config=AioConfig(connect_timeout=5, read_timeout=10, retries={"max_attempts": 1}),
    ) as client:
        yield client


# 요약 스트림용. Opus 5.5 는 thinking 이 항상 켜져 있고 기본 표시가 omitted 라 생각하는 동안 스트림에
# 바이트가 전혀 오지 않는다. 긴 트랜스크립트(78줄)에서 그 침묵이 기본 읽기 타임아웃 60초를 넘겨
# SocketTimeoutError 로 끊겼다(2026-10-09). 프런트 안전 타임아웃(5분)보다 길게 둔다.
SUMMARY_CLIENT_CONFIG = AioConfig(
    read_timeout=600,
    connect_timeout=10,
    retries={"max_attempts": 1},
)


@asynccontextmanager
async def bedrock_client(client_config: AioConfig | None = None):
    async with _session.create_client(
        "bedrock-runtime", region_name=config.REGION, config=client_config
    ) as client:
        yield client



@asynccontextmanager
async def polly_client():
    async with _session.create_client("polly", region_name=config.REGION) as client:
        yield client


# InvokeAgentRuntime 은 하나의 HTTP 응답을 수십 분간 스트리밍한다. 에이전트가 긴 도구(조립,
# 검색)를 돌리는 동안 이벤트가 없으면 기본 60초 읽기 타임아웃이 스트림을 끊으므로
# 그래프 시간 예산(2400초) 보다 길게 두고, 스트리밍 호출은 재시도하지 않는다.
AGENTCORE_CLIENT_CONFIG = AioConfig(
    read_timeout=2700,
    connect_timeout=10,
    retries={"max_attempts": 0},
)


@asynccontextmanager
async def agentcore_client():
    async with _session.create_client(
        "bedrock-agentcore", region_name=config.REGION, config=AGENTCORE_CLIENT_CONFIG
    ) as client:
        yield client


@asynccontextmanager
async def s3_client():
    async with _session.create_client("s3", region_name=config.REGION) as client:
        yield client


@asynccontextmanager
async def sesv2_client():
    async with _session.create_client("sesv2", region_name=config.REGION) as client:
        yield client


@asynccontextmanager
async def dynamodb_client():
    async with _session.create_client("dynamodb", region_name=config.REGION) as client:
        yield client


def normalize_language(lang: str) -> str:
    # "ko-KR" 같은 로케일 코드도 받는다 — 앞 2자만 잘라 넘기는 호출부에 의존하면
    # 새 호출부가 로케일을 그대로 넘겼을 때 조용히 "en" 으로 떨어진다.
    l = (lang or "").lower().split("-")[0]
    if l in ("ko", "korean", "kor"):
        return "ko"
    return "en"


# 한글 음절 + 자모. 완성형만 보면 "ㅋㅋ" 같은 자모 단독 표기를 놓친다.
_HANGUL_RE = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]")

# 리포트된 언어 코드를 뒤집기에 충분한 한글 비율.
SCRIPT_OVERRIDE_HANGUL_RATIO = 0.3


def hangul_ratio(text: str) -> float:
    """Fraction of the letters in text that are Hangul (0.0 when there are none).

    숫자·공백·문장부호는 분모에서 빼야 "2024년" 같은 짧은 발화가 영어로
    판정되지 않는다.
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if _HANGUL_RE.match(c)) / len(letters)


def resolve_detected_language(reported: str, text: str) -> str:
    """Decide the spoken language for auto-detect mode, preferring the script.

    Transcribe 의 identify_language 는 짧은 발화에서 언어를 뒤집는다. 한국어
    발화를 en-US 로 잘못 잡으면 영어 헛소리가 나오고, 그 상태로 번역 방향까지
    뒤집히면 결과가 완전히 망가진다. 트랜스크립트 자체가 한글이면 리포트된
    코드보다 글자를 믿는다 (반대 방향도 동일하게 적용).

    글자가 하나도 없으면(숫자만 있는 발화 등) 판단 근거가 없으므로 리포트된
    코드를 그대로 쓴다.
    """
    ratio = hangul_ratio(text)
    if ratio > SCRIPT_OVERRIDE_HANGUL_RATIO:
        return "ko"
    if ratio == 0.0 and any(c.isalpha() for c in text):
        return "en"
    return normalize_language(reported)


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
