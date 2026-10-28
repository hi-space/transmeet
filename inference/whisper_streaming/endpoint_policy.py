"""Meeting-caption boundaries shared in behavior with the Qwen adapter."""


def should_finalize(silence_ms: int, utterance_ms: int) -> bool:
    """Bound pauses at 800 ms and continuous utterances at 15 seconds."""
    return silence_ms >= 800 or utterance_ms >= 15000
