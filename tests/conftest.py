"""Shared test fixtures: settings, fakes, and a tiny valid PDF builder."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from job_recommendation_api.config import Settings

_MINIMAL_PDF = (
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


@pytest.fixture
def minimal_pdf_bytes() -> bytes:
    return _MINIMAL_PDF


@pytest.fixture
def test_settings() -> Settings:
    return Settings(openrouter_api_key=SecretStr("sk-test-key"), log_level="ERROR")


@pytest.fixture
def no_key_settings() -> Settings:
    return Settings(log_level="ERROR")


class FakeLLMClient:
    """Duck-typed LLMClient fake returning scripted payloads."""

    def __init__(self, payload: Any | None = None) -> None:
        self.payload = payload if payload is not None else default_payload()
        self.calls: list[tuple[list[dict[str, str]], dict[str, Any]]] = []
        self.closed = False

    async def complete(
        self, messages: list[dict[str, str]], *, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((messages, schema))
        return self.payload

    async def close(self) -> None:
        self.closed = True


def default_payload() -> dict[str, Any]:
    return {
        "summary": "Experienced backend engineer focused on Python and APIs.",
        "top_skills": ["Python", "FastAPI"],
        "jobs": [
            {
                "title": "Senior Backend Engineer",
                "fit_score": 0.87,
                "seniority_level": "senior",
                "rationale": "Strong Python and API background.",
                "key_skills": ["Python", "FastAPI"],
            }
        ],
        "education_materials": [
            {
                "topic": "System Design",
                "kind": "book",
                "title": "Designing Data-Intensive Applications",
                "rationale": "Reinforces distributed systems fundamentals.",
            }
        ],
    }


@pytest.fixture
def fake_llm() -> FakeLLMClient:
    return FakeLLMClient()


class FakeConverter:
    """Duck-typed DocumentConverter fake."""

    def __init__(self, markdown: str = "# Python Engineer\nExperienced.\njane@example.com") -> None:
        self.markdown = markdown
        self.calls: list[tuple[bytes, str]] = []

    def convert(self, pdf_bytes: bytes, *, name: str) -> str:
        self.calls.append((pdf_bytes, name))
        return self.markdown


@pytest.fixture
def fake_converter() -> FakeConverter:
    return FakeConverter()


def make_app(**overrides: Any) -> Any:
    """Build a configured FastAPI app for integration tests."""
    from job_recommendation_api.config import Settings
    from job_recommendation_api.main import create_app

    base: dict[str, Any] = {
        "openrouter_api_key": SecretStr("sk-test-key"),
        "log_level": "ERROR",
    }
    base.update(overrides)
    return create_app(Settings(**base))
