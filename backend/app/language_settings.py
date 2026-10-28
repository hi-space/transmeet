"""Recognition preferences and translation destinations are independent."""
from typing import Any


def parse_language_settings(provider: str, payload: dict[str, Any]) -> tuple[str, str]:
    """Return effective input and destination, preserving older clients' semantics."""
    source = payload.get("sourceLang")
    if source is None or source == "":
        source = "auto"
    if source not in ("auto", "ko", "en"):
        raise ValueError("지원하지 않는 음성 입력 언어입니다.")

    if "translationTarget" in payload:
        target = payload["translationTarget"]
        if target not in ("auto", "ko", "en"):
            raise ValueError("지원하지 않는 번역 대상입니다.")
    elif source == "auto":
        # Before independent destinations, an AUTO source always meant opposite.
        target = "auto"
    else:
        target = payload.get("targetLang") or "ko"
        if target not in ("ko", "en"):
            raise ValueError("지원하지 않는 번역 대상입니다.")

    # Resolve the legacy destination before applying the provider's input mode.
    return ("auto" if provider == "qwen3-asr" else source), target


def resolve_translation_target(selection: str, detected_language: str) -> str:
    """Resolve a destination only after the spoken language is known."""
    if selection == "auto":
        return "en" if detected_language == "ko" else "ko"
    return selection
