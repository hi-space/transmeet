"""Input preferences and translation destinations have independent meanings."""
import pytest

from app.language_settings import parse_language_settings, resolve_translation_target


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({}, ("auto", "auto")),
        ({"sourceLang": "auto", "targetLang": "ko"}, ("auto", "auto")),
        ({"sourceLang": "ko", "targetLang": "en"}, ("ko", "en")),
        ({"sourceLang": "en", "targetLang": "ko"}, ("en", "ko")),
        ({"sourceLang": "auto", "translationTarget": "ko"}, ("auto", "ko")),
        ({"sourceLang": "ko", "translationTarget": "auto"}, ("ko", "auto")),
        ({"sourceLang": "en", "translationTarget": "en", "targetLang": "ko"}, ("en", "en")),
    ],
)
def test_new_destinations_are_independent_and_legacy_directions_survive(payload, expected):
    assert parse_language_settings("transcribe", payload) == expected


def test_qwen_uses_auto_without_changing_a_legacy_fixed_translation_destination():
    assert parse_language_settings("qwen3-asr", {
        "sourceLang": "ko", "targetLang": "en",
    }) == ("auto", "en")


@pytest.mark.parametrize(
    "payload",
    [
        {"sourceLang": "ja"},
        {"sourceLang": []},
        {"sourceLang": False},
        {"translationTarget": "ja"},
        {"translationTarget": {}},
        {"sourceLang": "ko", "targetLang": "auto"},
    ],
)
def test_unsupported_language_values_are_rejected_before_starting_a_session(payload):
    with pytest.raises(ValueError):
        parse_language_settings("transcribe", payload)


@pytest.mark.parametrize(
    "selection,detected,expected",
    [
        ("auto", "ko", "en"),
        ("auto", "en", "ko"),
        ("ko", "ko", "ko"),
        ("ko", "en", "ko"),
        ("en", "ko", "en"),
        ("en", "en", "en"),
    ],
)
def test_translation_destination_depends_on_its_own_setting(selection, detected, expected):
    assert resolve_translation_target(selection, detected) == expected
