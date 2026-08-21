"""Environment-driven application configuration.

Secrets are only ever sourced from environment variables / ``.env`` and never
committed. ``load_settings()`` is a plain factory - NOT cached (no
``lru_cache``) so tests that construct isolated settings never leak state
across the test session.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

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

    # SH-001: API-key authentication. Keys are comma-separated in the env or
    # one-per-line in a file; the store keeps SHA-256 digests only.
    api_keys: str = ""
    api_keys_file: Path | None = None
    auth_required: bool = False
    anonymous_enabled: bool = True

    # SH-004: per-identity sliding-window rate limits.
    rate_limit_enabled: bool = True
    rate_limit_auth_requests: int = 60
    rate_limit_auth_window_seconds: float = 60.0
    rate_limit_anon_requests: int = 5
    rate_limit_anon_window_seconds: float = 3600.0
    rate_limit_max_tracked_identities: int = 10_000

    # SH-006: concurrency cap on the expensive conversion pipeline.
    convert_concurrency: int = 4

    # SH-007: PDF structural caps (checked before decode/OCR work scales
    # with attacker-chosen numbers).
    max_pdf_pages: int = 50
    max_images_per_page: int = 20
    max_page_inches: float = 30.0

    # SH-008: decoded-pixel ceiling, per-image dimension cap, and the
    # wall-clock deadline for the conversion stage.
    max_image_pixels: int = 50_000_000
    max_image_dimension: int = 10_000
    convert_deadline_seconds: float = 30.0

    # SH-010: interactive docs / OpenAPI availability.
    docs_enabled: bool | None = None

    # SH-012: CORS allowlist. Empty -> no CORS middleware at all.
    cors_origins: str = ""

    profile_model: str = "openai/gpt-4o-mini"
    ocr_temperature: float = 0.0
    llm_temperature: float = 0.0
    profile_fidelity: Literal["lenient", "strict"] = "lenient"
    max_ocr_pages: int = 10
    extraction_cache_max_entries: int = 256
    extraction_cache_ttl_seconds: int = 3600
    environment: Literal["development", "production"] = "production"

    def has_api_key(self) -> bool:
        return bool(self.openrouter_api_key.get_secret_value().strip())

    def docs_serving_enabled(self) -> bool:
        """SH-010: docs default to development mode; explicit override wins
        (``DOCS_ENABLED=true`` for staging, ``false`` to hide in dev)."""
        if self.docs_enabled is not None:
            return self.docs_enabled
        return self.environment == "development"

    def cors_origin_list(self) -> list[str]:
        """SH-012: parsed CORS origins (empty list = middleware not installed)."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


def load_settings() -> Settings:
    """Construct a ``Settings`` instance from the environment.

    Deliberately NOT cached and NOT a FastAPI dependency. FastAPI code obtains
    settings via ``api.deps.get_settings`` (ID-013), which reads the singleton
    built in ``create_app``'s lifespan.
    """
    return Settings()
