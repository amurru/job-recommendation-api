"""FP-003: LLMProfileExtractor tests (fake LLM, no network)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr
from tests.conftest import make_settings

from job_recommendation_api.config import Settings
from job_recommendation_api.errors import LLMInvalidOutputError
from job_recommendation_api.services.prompts import (
    PROFILE_SCHEMA,
    PROFILE_SYSTEM_PROMPT,
)
from job_recommendation_api.services.resume_profiler import LLMProfileExtractor

_MARKDOWN = "# Jane Doe\nSkills: Python, FastAPI\njane@example.com"

_PROFILE: dict[str, Any] = {
    "summary": "Python developer.",
    "skills": ["Python", "FastAPI"],
}


class _FakeLLM:
    def __init__(self, payloads: list[Any]) -> None:
        self._payloads = list(payloads)
        self.calls: list[list[dict[str, str]]] = []
        self.schemas: list[dict[str, Any]] = []

    async def complete(
        self, messages: list[dict[str, str]], *, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(messages)
        self.schemas.append(schema)
        return self._payloads.pop(0)

    async def close(self) -> None:
        pass


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "openrouter_api_key": SecretStr("sk-test"),
        "log_level": "ERROR",
    }
    base.update(overrides)
    return make_settings(**base)


@pytest.mark.asyncio
async def test_extract_happy_path() -> None:
    llm = _FakeLLM([_PROFILE])
    extractor = LLMProfileExtractor(llm, _settings())

    profile = await extractor.extract(_MARKDOWN)

    assert profile["skills"] == ["Python", "FastAPI"]
    assert len(llm.calls) == 1
    system_message = llm.calls[0][0]["content"]
    assert system_message == PROFILE_SYSTEM_PROMPT
    assert "<resume>" in llm.calls[0][1]["content"]
    assert _MARKDOWN in llm.calls[0][1]["content"]
    assert llm.schemas[0] == PROFILE_SCHEMA


@pytest.mark.asyncio
async def test_extract_retries_on_invalid_schema() -> None:
    llm = _FakeLLM([{"bad": "shape"}, _PROFILE])
    extractor = LLMProfileExtractor(llm, _settings())

    profile = await extractor.extract(_MARKDOWN)

    assert profile["skills"] == ["Python", "FastAPI"]
    assert len(llm.calls) == 2  # initial + corrective retry


@pytest.mark.asyncio
async def test_extract_raises_after_exhausted_retries() -> None:
    llm = _FakeLLM([{"bad": "shape"}, {"still": "bad"}, {"nope": 1}])
    extractor = LLMProfileExtractor(llm, _settings())

    with pytest.raises(LLMInvalidOutputError):
        await extractor.extract(_MARKDOWN)
    assert len(llm.calls) == 3  # max_attempts = retries + 1


@pytest.mark.asyncio
async def test_extract_lenient_mode_drops_unsupported_and_reports() -> None:
    fabricated = {**_PROFILE, "skills": ["Python", "FastAPI", "QuantumWarpDrive"]}
    llm = _FakeLLM([fabricated])
    extractor = LLMProfileExtractor(llm, _settings())

    profile = await extractor.extract(_MARKDOWN)

    assert profile["skills"] == ["Python", "FastAPI"]
    assert extractor.last_dropped_facts == ["QuantumWarpDrive"]


@pytest.mark.asyncio
async def test_extract_strict_mode_retries_then_raises() -> None:
    fabricated = {**_PROFILE, "skills": ["Python", "FabricatedSkill"]}
    llm = _FakeLLM([fabricated, fabricated, fabricated])
    extractor = LLMProfileExtractor(llm, _settings(profile_fidelity="strict"))

    with pytest.raises(LLMInvalidOutputError):
        await extractor.extract(_MARKDOWN)
    assert len(llm.calls) == 3  # bounded corrective retry, then fail
