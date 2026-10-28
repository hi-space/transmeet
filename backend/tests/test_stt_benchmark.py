"""A comparison must expose failed models and weight CER by characters."""
import pytest

from scripts import benchmark_stt as benchmark


def test_normalization_keeps_korean_content_and_numbers():
    assert benchmark.normalize_transcript("ＡＷＳ API, 안 녕! 2026.") == "awsapi안녕2026"
    assert benchmark.normalize_transcript("\u1100\u1161") == "가"


def test_character_score_counts_substitutions_insertions_and_deletions():
    assert benchmark.score_transcript("가나다", "가라다마") == {
        "errors": 2, "reference_chars": 3, "cer": 2 / 3,
    }
    assert benchmark.score_transcript("가나다", "가다")["errors"] == 1
    assert benchmark.score_transcript("안 녕!", "안녕")["cer"] == 0


def test_empty_reference_cannot_produce_a_misleading_zero_cer():
    assert benchmark.score_transcript("", "환각") == {
        "errors": 2, "reference_chars": 0, "cer": None,
    }


def test_summary_weights_errors_by_reference_length():
    rows = [
        {"provider": "nemotron", "errors": 1, "reference_chars": 10, "elapsed_seconds": 1, "audio_seconds": 2},
        {"provider": "nemotron", "errors": 0, "reference_chars": 90, "elapsed_seconds": 3, "audio_seconds": 6},
    ]
    summary = benchmark.summarize(rows)["nemotron"]
    assert summary["cer"] == 0.01
    assert summary["median_seconds"] == 2
    assert summary["real_time_factor"] == 0.5
    assert summary["successful_samples"] == 2


def test_failed_sample_invalidates_overall_cer_but_keeps_success_count():
    rows = [
        {"provider": "qwen3-asr", "errors": 0, "reference_chars": 20, "elapsed_seconds": 1, "audio_seconds": 2},
        {"provider": "qwen3-asr", "error": "endpoint unavailable"},
    ]
    summary = benchmark.summarize(rows)["qwen3-asr"]
    assert summary["cer"] is None
    assert summary["failed_samples"] == 1
    assert summary["successful_samples"] == 1


@pytest.mark.asyncio
async def test_provider_failure_is_recorded_instead_of_dropping_sample(monkeypatch):
    async def fail(*args):
        raise ValueError("QWEN_ASR_ENDPOINT is not configured")

    monkeypatch.setattr(benchmark, "recognize", fail)
    row = await benchmark.measure("qwen3-asr", b"audio", "ko", "정답", 2, 1)
    assert "QWEN_ASR_ENDPOINT" in row["error"]
    assert "cer" not in row
