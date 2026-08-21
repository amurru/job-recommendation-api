"""App factory, lifespan, middleware and the uvicorn runner."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

import anyio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from job_recommendation_api.api.errors import register_exception_handlers
from job_recommendation_api.api.middleware import security_headers_middleware
from job_recommendation_api.api.router import api_router
from job_recommendation_api.auth import ApiKeyStore
from job_recommendation_api.config import Settings, load_settings
from job_recommendation_api.errors import ConfigurationError
from job_recommendation_api.llm.client import OpenRouterLLMClient
from job_recommendation_api.ratelimit import SlidingWindowRateLimiter, WindowLimit
from job_recommendation_api.services.document_converter import MarkItDownConverter
from job_recommendation_api.services.extraction_cache import InMemoryExtractionCache
from job_recommendation_api.services.ocr_client import OpenRouterVisionClient
from job_recommendation_api.services.recommendation import RecommendationService
from job_recommendation_api.services.resume_profiler import LLMProfileExtractor

logger = logging.getLogger(__name__)

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# SH-009: allowlist charset + hard length cap for a client-supplied
# X-Request-ID. Anything else is replaced (never rejected) with a generated
# ID so odd proxies cannot break clients - and cannot inject into logs.
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_CORS_ALLOWED_METHODS = ["POST", "GET", "OPTIONS"]
_CORS_ALLOWED_HEADERS = ["Authorization", "Content-Type", "X-Request-ID"]


def _get_request_id(request: Request) -> str:
    incoming = request.headers.get("X-Request-ID")
    if incoming and _REQUEST_ID_PATTERN.fullmatch(incoming):
        return incoming
    return uuid.uuid4().hex


async def _request_id_middleware(request: Request, call_next: Any) -> Any:
    request_id = _get_request_id(request)
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = request_id
    return response


def _configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=(
            "%(asctime)s level=%(levelname)s logger=%(name)s request_id=%(request_id)s %(message)s"
        ),
    )

    class RequestIdFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.request_id = request_id_var.get() or "-"
            return True

    for handler in logging.getLogger().handlers:
        handler.addFilter(RequestIdFilter())


def _configure_cors(app: FastAPI, settings: Settings) -> None:
    """SH-012: explicit-origin allowlist or no CORS middleware at all.

    A configured ``*`` origin is a startup-time configuration error: it would
    silently re-create the permissive default this setting replaces.
    """
    origins = settings.cors_origin_list()
    if not origins:
        return
    if "*" in origins:
        raise ConfigurationError("CORS_ORIGINS must list explicit origins; '*' is not allowed.")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=_CORS_ALLOWED_METHODS,
        allow_headers=_CORS_ALLOWED_HEADERS,
        allow_credentials=False,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    ``settings`` is resolved once here; if not provided, ``load_settings()`` is
    the single construction point. A missing API key does not prevent startup:
    readiness is reported via ``/readyz`` and the recommendation path fails on
    request.
    """
    resolved = settings if settings is not None else load_settings()
    _configure_logging(resolved)
    docs_enabled = resolved.docs_serving_enabled()
    # SH-006: concurrency cap on the conversion pipeline, created once and
    # shared by the lifespan-built service and app.state.
    convert_limiter = anyio.CapacityLimiter(resolved.convert_concurrency)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ocr_client = OpenRouterVisionClient(resolved) if resolved.has_api_key() else None
        converter = MarkItDownConverter(
            ocr_client=ocr_client,
            ocr_model=resolved.ocr_model,
            max_ocr_pages=resolved.max_ocr_pages,
            max_pdf_pages=resolved.max_pdf_pages,
            max_page_inches=resolved.max_page_inches,
            max_images_per_page=resolved.max_images_per_page,
            max_image_pixels=resolved.max_image_pixels,
            max_image_dimension=resolved.max_image_dimension,
        )
        llm_client = OpenRouterLLMClient(resolved)
        await llm_client.start()
        profiler = LLMProfileExtractor(llm_client, resolved)
        extraction_cache = InMemoryExtractionCache(
            max_entries=resolved.extraction_cache_max_entries,
            ttl_seconds=resolved.extraction_cache_ttl_seconds,
        )
        api_key_store = ApiKeyStore.from_settings(resolved)
        rate_limiter = SlidingWindowRateLimiter(
            limits={
                "key": WindowLimit(
                    requests=resolved.rate_limit_auth_requests,
                    window_seconds=resolved.rate_limit_auth_window_seconds,
                ),
                "anonymous": WindowLimit(
                    requests=resolved.rate_limit_anon_requests,
                    window_seconds=resolved.rate_limit_anon_window_seconds,
                ),
            },
            max_tracked_identities=resolved.rate_limit_max_tracked_identities,
        )
        service = RecommendationService(
            converter,
            llm_client,
            model=resolved.openrouter_model,
            profiler=profiler,
            extraction_cache=extraction_cache,
            convert_limiter=convert_limiter,
            convert_deadline_seconds=resolved.convert_deadline_seconds,
        )
        app.state.settings = resolved
        app.state.converter = converter
        app.state.llm_client = llm_client
        app.state.profiler = profiler
        app.state.extraction_cache = extraction_cache
        app.state.recommendation_service = service
        app.state.api_key_store = api_key_store
        app.state.rate_limiter = rate_limiter
        app.state.convert_limiter = convert_limiter
        try:
            yield
        finally:
            await llm_client.close()
            if ocr_client is not None:
                ocr_client.close()

    app = FastAPI(
        title="Job Recommendation API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.settings = resolved
    # SH-001/SH-004: key store and limiter are pure in-memory state with no
    # async lifecycle, so they are also available outside the lifespan (the
    # TestClient pattern and dependency overrides rely on this).
    app.state.api_key_store = ApiKeyStore.from_settings(resolved)
    app.state.rate_limiter = SlidingWindowRateLimiter(
        limits={
            "key": WindowLimit(
                requests=resolved.rate_limit_auth_requests,
                window_seconds=resolved.rate_limit_auth_window_seconds,
            ),
            "anonymous": WindowLimit(
                requests=resolved.rate_limit_anon_requests,
                window_seconds=resolved.rate_limit_anon_window_seconds,
            ),
        },
        max_tracked_identities=resolved.rate_limit_max_tracked_identities,
    )

    _configure_cors(app, resolved)
    app.middleware("http")(_request_id_middleware)
    app.middleware("http")(security_headers_middleware)

    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()


def main() -> None:
    """Run the uvicorn server (entry point for the console script)."""
    import uvicorn

    settings: Settings = app.state.settings
    uvicorn.run(
        "job_recommendation_api.main:app",
        host="0.0.0.0",
        port=8000,
        log_level=settings.log_level.lower(),
    )
