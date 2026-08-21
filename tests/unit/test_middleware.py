"""SH-009/SH-013: request-ID validation and security headers."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from tests.api.test_integration import MINIMAL_PDF, _build_app, _FakeLLM

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class TestRequestIdValidation:
    def test_valid_uuid_passthrough(self) -> None:
        client: TestClient = _build_app(_FakeLLM())
        rid = "3f2b8c6a-1d4e-4f5a-9b7c-2e8d1f0a3b5c"
        resp = client.get("/healthz", headers={"X-Request-ID": rid})
        assert resp.headers["X-Request-ID"] == rid

    def test_valid_short_id_passthrough(self) -> None:
        client = _build_app(_FakeLLM())
        resp = client.get("/healthz", headers={"X-Request-ID": "abc_123-XYZ"})
        assert resp.headers["X-Request-ID"] == "abc_123-XYZ"

    def test_overlength_replaced(self) -> None:
        client = _build_app(_FakeLLM())
        resp = client.get("/healthz", headers={"X-Request-ID": "a" * 65})
        returned = resp.headers["X-Request-ID"]
        assert returned != "a" * 65
        assert _REQUEST_ID_PATTERN.fullmatch(returned)

    def test_hostile_characters_replaced(self) -> None:
        client = _build_app(_FakeLLM())
        for hostile in (
            "id\ninjected-log-line",  # log injection / newline
            'id"quote',  # quote
            "id space",  # whitespace
            "id;drop",  # punctuation
        ):
            resp = client.get("/healthz", headers={"X-Request-ID": hostile})
            returned = resp.headers["X-Request-ID"]
            assert returned != hostile
            assert _REQUEST_ID_PATTERN.fullmatch(returned), returned

    def test_absent_header_generates_id(self) -> None:
        client = _build_app(_FakeLLM())
        resp = client.get("/healthz")
        returned = resp.headers["X-Request-ID"]
        assert _REQUEST_ID_PATTERN.fullmatch(returned)
        assert len(returned) == 32  # uuid4().hex

    def test_exactly_64_chars_accepted(self) -> None:
        client = _build_app(_FakeLLM())
        rid = "x" * 64
        resp = client.get("/healthz", headers={"X-Request-ID": rid})
        assert resp.headers["X-Request-ID"] == rid


class TestSecurityHeaders:
    def test_headers_on_success(self) -> None:
        client = _build_app(_FakeLLM())
        resp = client.get("/healthz")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["Referrer-Policy"] == "no-referrer"
        assert resp.headers["X-Frame-Options"] == "DENY"

    def test_headers_on_error_response(self) -> None:
        """Headers must be present on error responses too (middleware wraps
        the whole chain)."""
        client = _build_app(_FakeLLM())
        resp = client.post(
            "/api/v1/recommendations",
            files={"file": ("resume.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 415
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["Referrer-Policy"] == "no-referrer"
        assert resp.headers["X-Frame-Options"] == "DENY"

    def test_headers_on_recommendations_success(self) -> None:
        client = _build_app(_FakeLLM())
        resp = client.post(
            "/api/v1/recommendations",
            files={"file": ("resume.pdf", MINIMAL_PDF, "application/pdf")},
        )
        assert resp.status_code == 200
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
