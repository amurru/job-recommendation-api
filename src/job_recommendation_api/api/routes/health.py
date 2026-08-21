"""Liveness and readiness routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from job_recommendation_api.api.deps import SettingsDep, get_optional_identity
from job_recommendation_api.auth import Identity

router = APIRouter(tags=["health"])

OptionalIdentityDep = Annotated[Identity | None, Depends(get_optional_identity)]


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: no dependencies, always 200 when the process is up."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(
    request: Request, settings: SettingsDep, identity: OptionalIdentityDep
) -> JSONResponse:
    """Readiness: 200 when the OpenRouter API key is configured, else 503.

    SH-013: the ``model`` field is disclosed only in development mode or to a
    caller presenting a valid API key; anonymous production callers get the
    minimal body.
    """
    if not settings.has_api_key():
        return JSONResponse(
            status_code=503,
            content={
                "status": "unready",
                "reason": "OPENROUTER_API_KEY is not set",
            },
        )
    show_model = settings.environment == "development" or (
        identity is not None and identity.kind == "key"
    )
    if show_model:
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "model": settings.openrouter_model},
        )
    return JSONResponse(status_code=200, content={"status": "ready"})
