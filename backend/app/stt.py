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


@dataclass(frozen=True)
class STTResult:
    text: str
    language: str = ""


def endpoint_for(provider: str) -> str:
    setting = _ENDPOINT_SETTINGS.get(provider)
    if setting is None:
        raise ValueError(f"지원하지 않는 SageMaker 음성 인식 엔진: {provider}")
    endpoint = getattr(config, setting).strip()
    if not endpoint:
        raise ValueError(f"{provider} 엔드포인트가 설정되지 않았습니다. {setting} 설정이 필요합니다.")
    return endpoint


def validate_provider(provider: str) -> None:
    if provider != "transcribe":
        endpoint_for(provider)


def inference_component_for(provider: str) -> str:
    settings = {
        "qwen3-asr": "QWEN_ASR_INFERENCE_COMPONENT",
        "nemotron": "NEMOTRON_ASR_INFERENCE_COMPONENT",
    }
    setting = settings.get(provider)
    return getattr(config, setting).strip() if setting else ""


def build_request(provider: str, audio: bytes, source_lang: str = "auto") -> dict[str, Any]:
    """Build only the model payload; clients cannot choose an endpoint name."""
    if source_lang not in _LANGUAGES:
        raise ValueError(f"지원하지 않는 음성 입력 언어: {source_lang}")
    if provider == "whisper":
        # Preserve the existing deployed Whisper handler's binary audio contract.
        return {"ContentType": "audio/wav", "Accept": "application/json", "Body": audio}
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
    async with sagemaker_client() as client:
        response = await client.invoke_endpoint(EndpointName=endpoint, **request)
        body = await response["Body"].read()
    return parse_response(provider, json.loads(body))
