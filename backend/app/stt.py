"""Request/response adapters for the SageMaker speech endpoints.

Contracts checked against the us-east-1 JumpStart catalogue on 2026-10-28.
Transcribe's persistent stream remains in ws_handler.
"""
import base64
import json
import re
from dataclasses import dataclass
from typing import Any

from .aws_clients import sagemaker_client
from .config import config


PROVIDERS = ("transcribe", "whisper", "qwen3-asr", "nemotron")
_ENDPOINT_SETTINGS = {
    "whisper": "WHISPER_ENDPOINT",
    "qwen3-asr": "QWEN_ASR_ENDPOINT",
    "nemotron": "NEMOTRON_ASR_ENDPOINT",
}
_LANGUAGES = {"ko": "ko-KR", "en": "en-US", "auto": "auto"}
_LANGUAGE_TAG = re.compile(r"\s*<([a-z]{2,3}-[A-Za-z]{2,4})>\s*$")
_STREAMING_SETTINGS = {
    "whisper": ("WHISPER_STREAMING", (1000, 2000)),
    "qwen3-asr": ("QWEN_STREAMING", (1000, 2000)),
    "nemotron": ("NEMOTRON_STREAMING", (320, 560)),
}


@dataclass(frozen=True)
class STTResult:
    text: str
    language: str = ""


def region_for(provider: str) -> str:
    """Resolve the batch model's server-configured region."""
    setting = {
        "qwen3-asr": "QWEN_ASR_REGION",
        "nemotron": "NEMOTRON_ASR_REGION",
    }.get(provider)
    override = getattr(config, setting, "") if setting else ""
    return override.strip() or config.REGION


def active_region_for(provider: str) -> str:
    """The optional streaming endpoint may be deployed separately from batch."""
    if is_streaming_provider(provider):
        prefix, _ = _STREAMING_SETTINGS[provider]
        fallback = region_for(provider) if provider == "qwen3-asr" else config.REGION
        return getattr(config, f"{prefix}_REGION").strip() or fallback
    return region_for(provider)


def endpoint_for(provider: str) -> str:
    setting = _ENDPOINT_SETTINGS.get(provider)
    if setting is None:
        raise ValueError(f"지원하지 않는 SageMaker 음성 인식 엔진: {provider}")
    endpoint = getattr(config, setting).strip()
    if not endpoint:
        raise ValueError(f"{provider} 엔드포인트가 설정되지 않았습니다. {setting} 설정이 필요합니다.")
    return endpoint


def validate_provider(provider: str, *, streaming: bool | None = None) -> None:
    if provider != "transcribe":
        if streaming is False:
            endpoint_for(provider)
        else:
            active_endpoint_for(provider)


def is_streaming_provider(provider: str) -> bool:
    settings = _STREAMING_SETTINGS.get(provider)
    return bool(settings and getattr(config, f"{settings[0]}_ENABLED"))


def streaming_chunk_ms(provider: str) -> int:
    prefix, allowed = _STREAMING_SETTINGS[provider]
    chunk_ms = getattr(config, f"{prefix}_CHUNK_MS")
    if type(chunk_ms) is not int or chunk_ms not in allowed:
        raise ValueError(f"{prefix}_CHUNK_MS는 {allowed[0]} 또는 {allowed[1]}이어야 합니다.")
    return chunk_ms


def active_endpoint_for(provider: str) -> str:
    """Recording endpoint; batch remains available for rollback and benchmarks."""
    if is_streaming_provider(provider):
        prefix, _ = _STREAMING_SETTINGS[provider]
        endpoint = getattr(config, f"{prefix}_ENDPOINT").strip()
        if not endpoint:
            raise ValueError(f"{provider} 엔드포인트가 설정되지 않았습니다. {prefix}_ENDPOINT 설정이 필요합니다.")
        streaming_chunk_ms(provider)
        return endpoint
    return endpoint_for(provider)


def inference_component_for(provider: str) -> str:
    settings = {
        "qwen3-asr": "QWEN_ASR_INFERENCE_COMPONENT",
        "nemotron": "NEMOTRON_ASR_INFERENCE_COMPONENT",
    }
    setting = settings.get(provider)
    return getattr(config, setting).strip() if setting else ""


def active_inference_component_for(provider: str) -> str:
    # The bidirectional SDK cannot address inference components.
    return "" if is_streaming_provider(provider) else inference_component_for(provider)


def parse_streaming_response(provider: str, payload: dict[str, Any]) -> STTResult:
    """Streaming containers emit hypotheses directly, without a chat envelope."""
    if provider == "nemotron":
        return parse_response(provider, payload)
    if provider not in ("whisper", "qwen3-asr"):
        raise ValueError(f"지원하지 않는 스트리밍 음성 인식 엔진: {provider}")
    text, language = payload.get("text"), payload.get("language", "")
    if not isinstance(text, str) or not isinstance(language, str):
        raise ValueError(f"{provider}: 잘못된 스트리밍 음성 인식 응답입니다.")
    return STTResult(text.strip(), language.strip())


def build_request(provider: str, audio: bytes, source_lang: str = "auto") -> dict[str, Any]:
    """Build only the model payload; clients cannot choose an endpoint name."""
    if source_lang not in _LANGUAGES:
        raise ValueError(f"지원하지 않는 음성 입력 언어: {source_lang}")
    if provider == "whisper":
        if source_lang == "auto":
            return {"ContentType": "audio/wav", "Accept": "application/json", "Body": audio}
        # The deployed JumpStart handler accepts hex WAV plus generation options.
        # Keep task=transcribe: translation belongs to the separate text pipeline.
        return {
            "ContentType": "application/json",
            "Accept": "application/json",
            "Body": json.dumps({
                "audio_input": audio.hex(),
                "language": {"ko": "korean", "en": "english"}[source_lang],
                "task": "transcribe",
            }).encode("utf-8"),
        }
    encoded = base64.b64encode(audio).decode("ascii")
    if provider == "qwen3-asr":
        # JumpStart's chat interface uses automatic language identification.
        # A top-level `language` is not part of this model's documented contract.
        payload = {
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "input_audio",
                    "input_audio": {"data": encoded, "format": "wav"},
                }],
            }],
            "max_tokens": 1024,
            "temperature": 0,
        }
    elif provider == "nemotron":
        # The container defaults to en-US unless language is explicitly supplied.
        payload = {"audio_base64": encoded, "language": _LANGUAGES[source_lang]}
    else:
        raise ValueError(f"지원하지 않는 SageMaker 음성 인식 엔진: {provider}")
    return {
        "ContentType": "application/json",
        "Accept": "application/json",
        "Body": json.dumps(payload).encode("utf-8"),
    }


def parse_response(provider: str, payload: Any) -> STTResult:
    """Normalize provider metadata without exposing it in subtitles."""
    if not isinstance(payload, dict):
        raise ValueError(f"{provider}: 음성 인식 응답이 JSON 객체가 아닙니다.")
    language = ""
    if provider == "qwen3-asr":
        try:
            choice = payload["choices"][0]
            text = choice["message"]["content"]
            if choice.get("finish_reason") == "length":
                raise ValueError("qwen3-asr: 토큰 한도로 인식 결과가 잘렸습니다.")
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("qwen3-asr: 잘못된 음성 인식 응답입니다.") from exc
        if isinstance(text, str) and "<asr_text>" in text:
            metadata, text = text.split("<asr_text>", 1)
            match = re.search(r"(?im)^language\s+([^\n]+)", metadata.strip())
            if match and match[1].strip().lower() != "none":
                language = match[1].strip()
    elif provider in ("whisper", "nemotron"):
        text = payload.get("text")
        language = payload.get("language") or ""
        if provider == "whisper" and isinstance(text, list) and all(isinstance(t, str) for t in text):
            text = " ".join(text)
        if provider == "nemotron" and isinstance(text, str):
            tag = _LANGUAGE_TAG.search(text)
            if tag:
                language = tag[1]
                text = text[:tag.start()]
            if language == "auto":
                language = ""
    else:
        raise ValueError(f"지원하지 않는 SageMaker 음성 인식 엔진: {provider}")
    if not isinstance(text, str) or not isinstance(language, str):
        raise ValueError(f"{provider}: 음성 인식 응답에 유효한 text/language가 없습니다.")
    return STTResult(text.strip(), language.strip())


async def transcribe_audio(provider: str, audio: bytes, source_lang: str = "auto") -> STTResult:
    endpoint = endpoint_for(provider)
    request = build_request(provider, audio, source_lang)
    component = inference_component_for(provider)
    if component:
        request["InferenceComponentName"] = component
    async with sagemaker_client(region_name=region_for(provider)) as client:
        response = await client.invoke_endpoint(EndpointName=endpoint, **request)
        body = await response["Body"].read()
    return parse_response(provider, json.loads(body))
