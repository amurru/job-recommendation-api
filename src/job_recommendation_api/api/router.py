"""Aggregation of the v1 API router."""

from __future__ import annotations

from fastapi import APIRouter

from job_recommendation_api.api.routes.health import router as health_router
from job_recommendation_api.api.routes.recommendations import (
    router as recommendations_router,
)

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(recommendations_router, prefix="/api/v1")
