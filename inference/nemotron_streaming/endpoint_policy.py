"""Utterance boundaries determine when live text becomes a saved sentence."""


def should_finalize(quiet_ms: int, utterance_ms: int) -> bool:
    """Decide whether a current utterance is ready for translation and storage.

    Shorter quiet periods feel faster but split hesitant speech more often.
    A maximum duration also bounds decoder memory during uninterrupted speech.
    """
    if utterance_ms >= 30_000:
        return True
    if utterance_ms < 400:
        return False
    return quiet_ms >= 700
