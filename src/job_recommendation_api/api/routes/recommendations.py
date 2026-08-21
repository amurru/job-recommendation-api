"""Recommendation endpoint: multipart PDF/photo upload -> validated guidance."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import JSONResponse

from job_recommendation_api.api.deps import (
    IdentityDep,
    RateLimiterDep,
    RecommendationServiceDep,
    SettingsDep,
)
from job_recommendation_api.config import Settings
from job_recommendation_api.errors import (
    DocumentTooLargeError,
    RateLimitedError,
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


def _meta_visible(settings: Settings, identity_kind: str, include_meta: bool) -> bool:
    """SH-013: ``?include_meta=true`` is honored only for keyed identities or
    development mode. Anonymous production requests silently get the default
    (meta omitted), never an error."""
    if settings.environment == "development":
        return True
    if not include_meta:
        return False
    return identity_kind == "key"


@router.post("/recommendations", response_model=RecommendationResponse)
async def create_recommendation(
    settings: SettingsDep,
    service: RecommendationServiceDep,
    identity: IdentityDep,
    limiter: RateLimiterDep,
    file: UploadFile = File(...),
    include_meta: bool = Query(
        False,
        description="Include the runtime `meta` diagnostics block in the response. "
        "Honored for authenticated requests and in development mode.",
    ),
) -> JSONResponse:
    """Analyze an uploaded resume PDF or photo and return career recommendations."""
    # SH-005: the limiter runs after identity resolution (SH-001/002) and
    # before any body parsing, conversion, or LLM work. Invalid credentials
    # never reach this point, so they consume no budget.
    decision = limiter.check(identity) if settings.rate_limit_enabled else None
    if decision is not None and not decision.allowed:
        raise RateLimitedError(
            f"Rate limit exceeded. Retry after {decision.retry_after_seconds} seconds.",
            retry_after_seconds=decision.retry_after_seconds,
            limit=decision.limit,
            remaining=decision.remaining,
            reset_epoch=decision.reset_epoch,
        )

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
    show_meta = _meta_visible(settings, identity.kind, include_meta)
    payload = result.model_dump(mode="json", exclude=None if show_meta else {"meta"})
    response = JSONResponse(content=payload)
    if result.meta is not None and result.meta.cache is not None:
        response.headers["X-Cache"] = result.meta.cache.upper()
    if decision is not None:
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        response.headers["X-RateLimit-Reset"] = str(int(decision.reset_epoch))
    return response
