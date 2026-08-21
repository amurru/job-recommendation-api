"""FastAPI dependency accessors for lifespan-built singletons.

These read from ``request.app.state`` only; nothing is constructed here.
Construction happens once in ``create_app``'s lifespan via the plain
``config.load_settings()`` factory and the concrete adapter classes.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request

from job_recommendation_api.auth import ApiKeyStore, Identity
from job_recommendation_api.config import Settings
from job_recommendation_api.errors import UnauthorizedError
from job_recommendation_api.llm.client import LLMClient
from job_recommendation_api.ratelimit import RateLimiter
from job_recommendation_api.services.document_converter import DocumentConverter
from job_recommendation_api.services.recommendation import RecommendationService


def _from_state(request: Request, key: str) -> Any:
    value = getattr(request.app.state, key, None)
    if value is None:
        raise RuntimeError(f"app.state.{key} is not initialized.")
    return value


def get_settings(request: Request) -> Settings:
    """Single FastAPI dependency for settings (never constructs them)."""
    return _from_state(request, "settings")


def get_converter(request: Request) -> DocumentConverter:
    return _from_state(request, "converter")


def get_llm_client(request: Request) -> LLMClient:
    return _from_state(request, "llm_client")


def get_recommendation_service(request: Request) -> RecommendationService:
    return _from_state(request, "recommendation_service")


def get_api_key_store(request: Request) -> ApiKeyStore:
    return _from_state(request, "api_key_store")


def get_rate_limiter(request: Request) -> RateLimiter:
    return _from_state(request, "rate_limiter")


def get_identity(request: Request) -> Identity:
    """Resolve the caller identity (SH-001).

    - ``Authorization: Bearer <key>`` with a valid key -> keyed identity.
    - Invalid key or non-Bearer scheme -> 401 ``UnauthorizedError``.
    - No header -> anonymous identity when allowed, else 401.

    Runs before any body parsing, conversion, or LLM call; invalid
    credentials consume no rate-limit budget. The identity (never the key)
    is attached to request state for logging.
    """
    settings: Settings = get_settings(request)
    header = request.headers.get("Authorization")

    if header is not None:
        scheme, _, credentials = header.partition(" ")
        if scheme.lower() != "bearer" or not credentials.strip():
            raise UnauthorizedError(
                "The Authorization header must use the Bearer scheme with an API key."
            )
        store: ApiKeyStore = get_api_key_store(request)
        identity = store.verify(credentials)
        if identity is None:
            raise UnauthorizedError("The supplied API key is not valid.")
        request.state.identity = identity
        return identity

    if settings.auth_required or not settings.anonymous_enabled:
        raise UnauthorizedError("A valid API key is required.")
    identity = Identity(kind="anonymous", ip=request.client.host if request.client else None)
    request.state.identity = identity
    return identity


def get_optional_identity(request: Request) -> Identity | None:
    """Soft identity probe for diagnostics gating (SH-013).

    Never raises: health/diagnostic surfaces stay unauthenticated. A valid
    key upgrades the response; anything else is treated as anonymous.
    """
    header = request.headers.get("Authorization")
    if header is None:
        return None
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        return None
    store: ApiKeyStore = get_api_key_store(request)
    return store.verify(credentials)


SettingsDep = Annotated[Settings, Depends(get_settings)]
ConverterDep = Annotated[DocumentConverter, Depends(get_converter)]
LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]
RecommendationServiceDep = Annotated[RecommendationService, Depends(get_recommendation_service)]
ApiKeyStoreDep = Annotated[ApiKeyStore, Depends(get_api_key_store)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
IdentityDep = Annotated[Identity, Depends(get_identity)]
