import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from inference.qwen_streaming.endpoint_policy import should_finalize


def test_a_short_pause_keeps_the_utterance_open_until_800_ms():
    assert not should_finalize(799, 5000)
    assert should_finalize(800, 5000)


def test_continuous_speech_is_bounded_at_15_seconds():
    assert not should_finalize(0, 14999)
    assert should_finalize(0, 15000)
