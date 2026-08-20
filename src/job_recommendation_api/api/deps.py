"""FastAPI dependency accessors for lifespan-built singletons.

These read from ``request.app.state`` only; nothing is constructed here.
Construction happens once in ``create_app``'s lifespan via the plain
``config.load_settings()`` factory and the concrete adapter classes.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request

from job_recommendation_api.config import Settings
from job_recommendation_api.llm.client import LLMClient
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


SettingsDep = Annotated[Settings, Depends(get_settings)]
ConverterDep = Annotated[DocumentConverter, Depends(get_converter)]
LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]
RecommendationServiceDep = Annotated[RecommendationService, Depends(get_recommendation_service)]
