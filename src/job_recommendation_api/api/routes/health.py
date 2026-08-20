"""Liveness and readiness routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from job_recommendation_api.api.deps import SettingsDep

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: no dependencies, always 200 when the process is up."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, settings: SettingsDep) -> JSONResponse:
    """Readiness: 200 when the OpenRouter API key is configured, else 503."""
    if settings.has_api_key():
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "model": settings.openrouter_model},
        )
    return JSONResponse(
        status_code=503,
        content={
            "status": "unready",
            "reason": "OPENROUTER_API_KEY is not set",
        },
    )
