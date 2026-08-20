"""ID-015: API integration tests (health + recommendations)."""

from __future__ import annotations

import io
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from job_recommendation_api.api.deps import get_recommendation_service
from job_recommendation_api.config import Settings
from job_recommendation_api.errors import LLMInvalidOutputError
from job_recommendation_api.main import create_app
from job_recommendation_api.services.recommendation import RecommendationService

MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
    b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
    b"4 0 obj<</Length 60>>stream\n"
    b"BT /F1 12 Tf 72 720 Td (Python backend engineer) Tj ET\n"
    b"endstream endobj\n"
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"xref\n"
    b"0 6\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"0000000244 00000 n \n"
    b"0000000333 00000 n \n"
    b"trailer<</Size 6/Root 1 0 R>>\n"
    b"startxref\n"
    b"408\n"
    b"%%EOF\n"
)


def _valid_payload() -> dict[str, Any]:
    return {
        "summary": "Backend engineer.",
        "top_skills": ["Python"],
        "jobs": [
            {
                "title": "Backend Engineer",
                "fit_score": 0.9,
                "seniority_level": "senior",
                "rationale": "Great fit.",
                "key_skills": ["Python"],
            }
        ],
        "education_materials": [
            {
                "topic": "System Design",
                "kind": "book",
                "title": "DDIA",
                "rationale": "Useful.",
            }
        ],
    }


class _FakeConverter:
    def __init__(self, markdown: str = "# Jane\nPython developer\njane@example.com") -> None:
        self._markdown = markdown

    def convert(self, document_bytes: bytes, *, name: str) -> str:
        return self._markdown


class _FakeLLM:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        errors: list[Exception] | None = None,
    ) -> None:
        self._payload = payload if payload is not None else _valid_payload()
        self._errors = list(errors or [])
        self.calls = 0

    async def complete(
        self, messages: list[dict[str, str]], *, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return self._payload

    async def close(self) -> None:
        pass


def _build_app(
    fake: _FakeLLM, converter: _FakeConverter | None = None, **settings_overrides: object
) -> TestClient:
    base: dict[str, object] = {
        "openrouter_api_key": SecretStr("sk-test-key"),
        "log_level": "ERROR",
    }
    base.update(settings_overrides)
    app = create_app(Settings(**base))  # type: ignore[arg-type]
    app.dependency_overrides[get_recommendation_service] = lambda: RecommendationService(
        converter or _FakeConverter(), fake, model="test/model"
    )
    return TestClient(app)


class TestHealth:
    def test_healthz_ok(self) -> None:
        client = _build_app(_FakeLLM())
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_readyz_ready_with_key(self) -> None:
        client = _build_app(_FakeLLM())
        resp = client.get("/readyz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert body["model"] == "openai/gpt-4o-mini"

    def test_readyz_unready_without_key(self) -> None:
        app = create_app(Settings(log_level="ERROR"))
        client = TestClient(app)
        resp = client.get("/readyz")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "unready"
        assert "OPENROUTER_API_KEY" in body["reason"]


class TestRecommendations:
    def test_recommendation_200(self) -> None:
        fake = _FakeLLM()
        client = _build_app(fake)
        resp = client.post(
            "/api/v1/recommendations",
            files={"file": ("resume.pdf", MINIMAL_PDF, "application/pdf")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["analysis"]["summary"] == "Backend engineer."
        assert body["analysis"]["jobs"][0]["title"] == "Backend Engineer"
        assert body["meta"]["model"] == "test/model"
        assert fake.calls == 1

    def test_recommendation_415_wrong_content_type(self) -> None:
        client = _build_app(_FakeLLM())
        resp = client.post(
            "/api/v1/recommendations",
            files={"file": ("resume.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 415
        assert resp.json()["error"]["code"] == "unsupported_media_type"

    def test_recommendation_png_photo_accepted(self) -> None:
        buf = io.BytesIO()
        Image.new("RGB", (32, 32), color="white").save(buf, format="PNG")
        client = _build_app(_FakeLLM())
        resp = client.post(
            "/api/v1/recommendations",
            files={"file": ("photo.png", buf.getvalue(), "image/png")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["analysis"]["summary"] == "Backend engineer."

    def test_recommendation_413_oversized(self) -> None:
        client = _build_app(_FakeLLM(), max_upload_bytes=1024)
        big = MINIMAL_PDF + b"x" * 2048
        resp = client.post(
            "/api/v1/recommendations",
            files={"file": ("resume.pdf", big, "application/pdf")},
        )
        assert resp.status_code == 413
        assert resp.json()["error"]["code"] == "document_too_large"

    def test_recommendation_422_invalid_llm_output(self) -> None:
        fake = _FakeLLM(errors=[LLMInvalidOutputError("bad output")])
        client = _build_app(fake)
        resp = client.post(
            "/api/v1/recommendations",
            files={"file": ("resume.pdf", MINIMAL_PDF, "application/pdf")},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "llm_invalid_output"

    def test_recommendation_422_not_a_resume(self) -> None:
        fake = _FakeLLM()
        converter = _FakeConverter(markdown="Student Information\nStudent USER ID: ammar_94038")
        client = _build_app(fake, converter=converter)
        resp = client.post(
            "/api/v1/recommendations",
            files={"file": ("resume.pdf", MINIMAL_PDF, "application/pdf")},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "not_a_resume"
        assert fake.calls == 0  # LLM must never be called for a non-resume

    def test_recommendation_error_envelope_shape(self) -> None:
        fake = _FakeLLM(errors=[LLMInvalidOutputError("bad output")])
        client = _build_app(fake)
        resp = client.post(
            "/api/v1/recommendations",
            files={"file": ("resume.pdf", MINIMAL_PDF, "application/pdf")},
        )
        body = resp.json()
        assert set(body.keys()) == {"error"}
        assert set(body["error"].keys()) == {"code", "message"}

    def test_recommendation_missing_file_422(self) -> None:
        client = _build_app(_FakeLLM())
        resp = client.post("/api/v1/recommendations")
        assert resp.status_code == 422

    def test_request_id_header_present(self) -> None:
        client = _build_app(_FakeLLM())
        resp = client.get("/healthz", headers={"X-Request-ID": "abc123"})
        assert resp.headers.get("X-Request-ID") == "abc123"
