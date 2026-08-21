"""HTTP middleware: security headers (SH-013) and request-ID handling.

Security headers are static strings set on every response, including errors
(the middleware wraps the whole call chain). ``Strict-Transport-Security`` is
deliberately NOT set in-app: the app does not terminate TLS, so HSTS belongs
at the reverse proxy.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request, Response

_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


async def security_headers_middleware(request: Request, call_next: Any) -> Any:
    """Add the baseline security headers to every response."""
    response: Response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response
