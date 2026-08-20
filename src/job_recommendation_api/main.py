"""App factory, lifespan, middleware and the uvicorn runner."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from job_recommendation_api.api.errors import register_exception_handlers
from job_recommendation_api.api.router import api_router
from job_recommendation_api.config import Settings, load_settings
from job_recommendation_api.llm.client import OpenRouterLLMClient
from job_recommendation_api.services.document_converter import MarkItDownConverter
from job_recommendation_api.services.extraction_cache import InMemoryExtractionCache
from job_recommendation_api.services.ocr_client import OpenRouterVisionClient
from job_recommendation_api.services.recommendation import RecommendationService
from job_recommendation_api.services.resume_profiler import LLMProfileExtractor

logger = logging.getLogger(__name__)

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def _get_request_id(request: Request) -> str:
    incoming = request.headers.get("X-Request-ID")
    if incoming:
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


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    ``settings`` is resolved once here; if not provided, ``load_settings()`` is
    the single construction point. A missing API key does not prevent startup:
    readiness is reported via ``/readyz`` and the recommendation path fails on
    request.
    """
    resolved = settings if settings is not None else load_settings()
    _configure_logging(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ocr_client = OpenRouterVisionClient(resolved) if resolved.has_api_key() else None
        converter = MarkItDownConverter(
            ocr_client=ocr_client,
            ocr_model=resolved.ocr_model,
            max_ocr_pages=resolved.max_ocr_pages,
        )
        llm_client = OpenRouterLLMClient(resolved)
        await llm_client.start()
        profiler = LLMProfileExtractor(llm_client, resolved)
        extraction_cache = InMemoryExtractionCache(
            max_entries=resolved.extraction_cache_max_entries,
            ttl_seconds=resolved.extraction_cache_ttl_seconds,
        )
        service = RecommendationService(
            converter,
            llm_client,
            model=resolved.openrouter_model,
            profiler=profiler,
            extraction_cache=extraction_cache,
        )
        app.state.settings = resolved
        app.state.converter = converter
        app.state.llm_client = llm_client
        app.state.profiler = profiler
        app.state.extraction_cache = extraction_cache
        app.state.recommendation_service = service
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
    )
    app.state.settings = resolved

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(_request_id_middleware)

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
