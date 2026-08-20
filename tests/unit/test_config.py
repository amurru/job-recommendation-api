"""ID-002: settings/config tests."""

from __future__ import annotations

from pydantic import SecretStr
from tests.conftest import make_settings

from job_recommendation_api.config import Settings, load_settings


def test_defaults() -> None:
    settings = make_settings(openrouter_api_key=SecretStr("sk-test"), log_level="DEBUG")
    assert settings.openrouter_model == "openai/gpt-4o-mini"
    assert settings.ocr_model == "openai/gpt-4o-mini"
    assert settings.llm_timeout_seconds == 60.0
    assert settings.llm_max_tokens == 4096
    assert settings.max_upload_bytes == 10 * 1024 * 1024
    assert settings.log_level == "DEBUG"
    assert settings.has_api_key()


def test_extraction_fidelity_defaults() -> None:
    """FP-001: new settings default to deterministic, bounded values."""
    settings = make_settings()
    assert settings.profile_model == "openai/gpt-4o-mini"
    assert settings.ocr_temperature == 0.0
    assert settings.llm_temperature == 0.0
    assert settings.profile_fidelity == "lenient"
    assert settings.max_ocr_pages == 10
    assert settings.extraction_cache_max_entries == 256
    assert settings.extraction_cache_ttl_seconds == 3600


def test_extraction_fidelity_overrides() -> None:
    settings = make_settings(
        profile_model="other/model",
        ocr_temperature=0.3,
        llm_temperature=0.5,
        profile_fidelity="strict",
        max_ocr_pages=3,
        extraction_cache_max_entries=8,
        extraction_cache_ttl_seconds=60,
    )
    assert settings.profile_model == "other/model"
    assert settings.ocr_temperature == 0.3
    assert settings.llm_temperature == 0.5
    assert settings.profile_fidelity == "strict"
    assert settings.max_ocr_pages == 3
    assert settings.extraction_cache_max_entries == 8
    assert settings.extraction_cache_ttl_seconds == 60


def test_invalid_fidelity_mode_rejected() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        make_settings(profile_fidelity="yolo")


def test_has_api_key_false_when_empty() -> None:
    assert make_settings(log_level="ERROR").has_api_key() is False


def test_secret_is_masked_in_repr() -> None:
    settings = make_settings(openrouter_api_key=SecretStr("hunter2"), log_level="ERROR")
    assert "hunter2" not in repr(settings.openrouter_api_key)


def test_load_settings_returns_instance() -> None:
    assert isinstance(load_settings(), Settings)
