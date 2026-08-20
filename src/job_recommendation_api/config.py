"""Environment-driven application configuration.

Secrets are only ever sourced from environment variables / ``.env`` and never
committed. ``load_settings()`` is a plain factory - NOT cached (no
``lru_cache``) so tests that construct isolated settings never leak state
across the test session.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Runtime configuration, populated from environment / ``.env``.

    Environment variables always win over ``.env`` values (pydantic-settings
    precedence). Set ``env_prefix`` to none here so the documented variables
    (``OPENROUTER_API_KEY`` etc.) are stable and unambiguous.

    The API key is optional at construction time so the app imports and starts
    without it; readiness is surfaced via ``/readyz`` and the recommendation
    path fails fast on request if it is missing.
    """

    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_model: str = "openai/gpt-4o-mini"
    ocr_model: str = "openai/gpt-4o-mini"
    llm_timeout_seconds: float = 60.0
    llm_max_tokens: int = 4096
    max_upload_bytes: int = 10 * 1024 * 1024
    log_level: str = "INFO"

    def has_api_key(self) -> bool:
        return bool(self.openrouter_api_key.get_secret_value().strip())


def load_settings() -> Settings:
    """Construct a ``Settings`` instance from the environment.

    Deliberately NOT cached and NOT a FastAPI dependency. FastAPI code obtains
    settings via ``api.deps.get_settings`` (ID-013), which reads the singleton
    built in ``create_app``'s lifespan.
    """
    return Settings()
