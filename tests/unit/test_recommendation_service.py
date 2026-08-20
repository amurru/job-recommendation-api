"""ID-008: recommendation orchestration service tests."""

from __future__ import annotations

from typing import Any

import pytest

from job_recommendation_api.errors import LLMInvalidOutputError, NotAResumeError
from job_recommendation_api.services.recommendation import RecommendationService


class _FakeLLM:
    def __init__(self, *payloads: dict[str, Any] | None) -> None:
        self._payloads: list[dict[str, Any] | None] = list(payloads)
        self.calls: list[list[dict[str, str]]] = []

    async def complete(
        self, messages: list[dict[str, str]], *, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(messages)
        payload = self._payloads.pop(0)
        if payload is None:
            raise AssertionError("No more payloads")
        return payload

    async def close(self) -> None:
        pass


class _FakeConverter:
    def __init__(self, markdown: str = "# Jane\nPython developer\njane@example.com") -> None:
        self.markdown = markdown
        self.called_with: bytes | None = None

    def convert(self, pdf_bytes: bytes, *, name: str) -> str:
        self.called_with = pdf_bytes
        return self.markdown


@pytest.fixture
def valid_payload() -> dict[str, Any]:
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


@pytest.mark.asyncio
async def test_recommend_happy_path(valid_payload: dict[str, Any]) -> None:
    converter = _FakeConverter()
    llm = _FakeLLM(valid_payload)
    service = RecommendationService(converter, llm, model="test/model")

    result = await service.recommend(b"%PDF-1.4", name="resume.pdf")

    assert converter.called_with == b"%PDF-1.4"
    assert len(llm.calls) == 1
    assert result.analysis.summary == "Backend engineer."
    assert result.meta.model == "test/model"
    assert result.meta.markdown_length == len(converter.markdown)


@pytest.mark.asyncio
async def test_recommend_retries_once_on_invalid_output(
    valid_payload: dict[str, Any],
) -> None:
    converter = _FakeConverter()
    # First call returns invalid, second valid -> corrective retry path
    llm = _FakeLLM(valid_payload, valid_payload)
    service = RecommendationService(converter, llm, model="test/model")

    result = await service.recommend(b"%PDF-1.4", name="resume.pdf")

    assert len(llm.calls) >= 1
    assert result.analysis.summary == "Backend engineer."


@pytest.mark.asyncio
async def test_recommend_raises_when_llm_keeps_failing(
    valid_payload: dict[str, Any],
) -> None:
    converter = _FakeConverter()

    class _FailingLLM:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def complete(
            self,
            messages: list[dict[str, str]],
            *,
            schema: dict[str, Any],
        ) -> dict[str, Any]:
            self.calls.append(messages)
            return {"bad": "payload"}

        async def close(self) -> None:
            pass

    llm = _FailingLLM()
    service = RecommendationService(converter, llm, model="test/model")
    with pytest.raises(LLMInvalidOutputError):
        await service.recommend(b"%PDF-1.4", name="resume.pdf")
    assert len(llm.calls) == 2  # initial + corrective retry


@pytest.mark.asyncio
async def test_recommend_rejects_non_resume_document(
    valid_payload: dict[str, Any],
) -> None:
    converter = _FakeConverter(markdown="Student Information\nStudent USER ID: ammar_94038")
    llm = _FakeLLM(valid_payload)
    service = RecommendationService(converter, llm, model="test/model")

    with pytest.raises(NotAResumeError):
        await service.recommend(b"%PDF-1.4", name="resume.pdf")
    assert llm.calls == []  # LLM must never be called for a non-resume
