"""Exception -> HTTP error envelope handlers.

Domain errors are transport-agnostic; this layer maps them to status codes and
a uniform ``{"error": {"code", "message"}}`` envelope. Full detail is logged
server-side; clients never see stack traces.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from job_recommendation_api.errors import AppError, RateLimitedError, UnauthorizedError

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all error handlers to the app."""

    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        logger.error(
            "AppError code=%s detail=%s path=%s",
            exc.code,
            exc.detail,
            request.url.path,
            exc_info=True,
        )
        headers: dict[str, str] = {}
        if isinstance(exc, UnauthorizedError):
            headers["WWW-Authenticate"] = "Bearer"
        if isinstance(exc, RateLimitedError):
            if exc.retry_after_seconds > 0:
                headers["Retry-After"] = str(exc.retry_after_seconds)
            headers["X-RateLimit-Limit"] = str(exc.limit)
            headers["X-RateLimit-Remaining"] = str(exc.remaining)
            headers["X-RateLimit-Reset"] = str(int(exc.reset_epoch))
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.detail}},
            headers=headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # SH-011: full pydantic detail stays server-side; the client body is
        # generic so reflected inputs and internal model paths never echo back.
        logger.warning(
            "RequestValidationError errors=%s path=%s",
            exc.errors(),
            request.url.path,
        )
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed.",
                }
            },
        )
