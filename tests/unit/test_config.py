"""ID-002: settings/config tests."""

from __future__ import annotations

from pydantic import SecretStr

from job_recommendation_api.config import Settings, load_settings


def test_defaults() -> None:
    settings = Settings(openrouter_api_key=SecretStr("sk-test"), log_level="DEBUG")
    assert settings.openrouter_model == "openai/gpt-4o-mini"
    assert settings.ocr_model == "openai/gpt-4o-mini"
    assert settings.llm_timeout_seconds == 60.0
    assert settings.llm_max_tokens == 4096
    assert settings.max_upload_bytes == 10 * 1024 * 1024
    assert settings.log_level == "DEBUG"
    assert settings.has_api_key()


def test_has_api_key_false_when_empty() -> None:
    assert Settings(log_level="ERROR").has_api_key() is False


def test_secret_is_masked_in_repr() -> None:
    settings = Settings(openrouter_api_key=SecretStr("hunter2"), log_level="ERROR")
    assert "hunter2" not in repr(settings.openrouter_api_key)


def test_load_settings_returns_instance() -> None:
    assert isinstance(load_settings(), Settings)
