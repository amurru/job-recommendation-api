"""Recommendation endpoint: multipart PDF upload -> validated guidance."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, UploadFile

from job_recommendation_api.api.deps import RecommendationServiceDep, SettingsDep
from job_recommendation_api.errors import (
    DocumentTooLargeError,
    UnsupportedMediaTypeError,
)
from job_recommendation_api.schemas.recommendation import RecommendationResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["recommendations"])

_PDF_CONTENT_TYPE = "application/pdf"


@router.post("/recommendations", response_model=RecommendationResponse)
async def create_recommendation(
    settings: SettingsDep,
    service: RecommendationServiceDep,
    file: UploadFile = File(...),
) -> RecommendationResponse:
    """Analyze an uploaded resume PDF and return career recommendations."""
    if file.content_type != _PDF_CONTENT_TYPE:
        raise UnsupportedMediaTypeError("Only application/pdf documents are supported.")

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
    return result
