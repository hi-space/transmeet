"""Meeting-caption boundaries, independent of network frames and Qwen tokens."""


def should_finalize(silence_ms: int, utterance_ms: int) -> bool:
    """Return whether the current utterance should be finalized.

    The proposed defaults are 800 ms of silence or 15 seconds of continuous
    speech. A longer silence threshold groups pauses into fewer meeting lines,
    at the cost of delaying translation. The duration limit bounds Qwen's
    cumulative audio context.
    """
    return silence_ms >= 800 or utterance_ms >= 15000
