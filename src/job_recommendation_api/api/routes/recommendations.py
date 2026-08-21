"""Recommendation endpoint: multipart PDF/photo upload -> validated guidance."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import JSONResponse

from job_recommendation_api.api.deps import RecommendationServiceDep, SettingsDep
from job_recommendation_api.errors import (
    DocumentTooLargeError,
    UnsupportedMediaTypeError,
)
from job_recommendation_api.schemas.recommendation import RecommendationResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["recommendations"])

# Photo resumes may arrive as PDFs or as image files (jpeg/png/webp).
_SUPPORTED_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)


@router.post("/recommendations", response_model=RecommendationResponse)
async def create_recommendation(
    settings: SettingsDep,
    service: RecommendationServiceDep,
    file: UploadFile = File(...),
    include_meta: bool = Query(
        False,
        description="Include the runtime `meta` diagnostics block in the response. "
        "Always included when the server runs in development mode.",
    ),
) -> JSONResponse:
    """Analyze an uploaded resume PDF or photo and return career recommendations."""
    if file.content_type not in _SUPPORTED_CONTENT_TYPES:
        raise UnsupportedMediaTypeError(
            "Only PDF and image documents (jpeg, png, webp) are supported."
        )

    # Read up to cap + 1 byte so an over-limit file is detected without
    # buffering unbounded memory.
    limit = settings.max_upload_bytes
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise DocumentTooLargeError(
            f"The uploaded file exceeds the maximum allowed size of {limit} bytes."
        )

    name = file.filename or "resume.pdf"
    result = await service.recommend(content, name=name)
    show_meta = include_meta or settings.environment == "development"
    payload = result.model_dump(mode="json", exclude=None if show_meta else {"meta"})
    response = JSONResponse(content=payload)
    if result.meta is not None and result.meta.cache is not None:
        response.headers["X-Cache"] = result.meta.cache.upper()
    return response
