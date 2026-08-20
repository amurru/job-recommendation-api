"""Structured profile extraction: markdown -> validated ``ResumeProfile``.

The profiler owns its own bounded corrective retry (mirroring
``RecommendationService``'s loop - the retry is not shared code). Extraction
runs at temperature 0 and every output is re-validated with Pydantic; the raw
model JSON is never trusted directly.

The fidelity checkpoint (``check_fidelity``) is deterministic: no extra LLM
call. In lenient mode unsupported facts are dropped and reported; in strict
mode they raise into the profiler's corrective retry.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from job_recommendation_api.config import Settings
from job_recommendation_api.errors import LLMInvalidOutputError
from job_recommendation_api.llm.client import LLMClient
from job_recommendation_api.schemas.profile import ResumeProfile
from job_recommendation_api.services.prompts import (
    PROFILE_SCHEMA,
    PROFILE_SYSTEM_PROMPT,
    build_profile_prompt,
)

logger = logging.getLogger(__name__)

Message = dict[str, str]

_CORRECTIVE_PROMPT = (
    "Your previous response was invalid. Return ONLY a single JSON object "
    "that matches the expected profile schema exactly: no markdown fences, no "
    "commentary, no extra keys, and no facts absent from the resume text."
)


class ProfileExtractor(Protocol):
    """Async markdown -> validated profile dict."""

    async def extract(self, markdown: str) -> dict[str, Any]: ...


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace for containment checks."""
    return " ".join(text.lower().split())


def _significant_tokens(text: str) -> set[str]:
    """Word tokens longer than 2 chars; punctuation stripped so markdown
    bullets/commas do not break token containment."""
    return {token for token in re.findall(r"\w+", text.lower()) if len(token) > 2}


@dataclass
class FidelityReport:
    """Outcome of the deterministic profile-vs-source checkpoint."""

    dropped_facts: list[str] = field(default_factory=list)
    supported_facts: int = 0
    cleaned_profile: dict[str, Any] = field(default_factory=dict)


def _fact_supported(markdown_normalized: str, markdown_tokens: set[str], fact: str) -> bool:
    normalized = _normalize(fact)
    if normalized in markdown_normalized:
        return True
    tokens = _significant_tokens(fact)
    if tokens and tokens <= markdown_tokens:
        return True
    return False


def check_fidelity(markdown: str, profile: dict[str, Any]) -> FidelityReport:
    """Verify discrete list facts have textual support in the markdown.

    ``summary``, ``current_title``, ``location``, and ``years_experience``
    are trusted (short prose is not substring-checkable); the checkpoint
    targets skills, education, languages, and certifications.
    """
    markdown_normalized = _normalize(markdown)
    markdown_tokens = _significant_tokens(markdown)

    dropped: list[str] = []
    supported = 0

    def _check(fact: str) -> bool:
        nonlocal supported
        if _fact_supported(markdown_normalized, markdown_tokens, fact):
            supported += 1
            return True
        dropped.append(fact)
        return False

    checked_profile: dict[str, Any] = dict(profile)
    for skill in profile.get("skills", []):
        _check(skill)
    for language in profile.get("languages", []):
        _check(language)
    for certification in profile.get("certifications", []):
        _check(certification)
    for entry in profile.get("education", []):
        if isinstance(entry, dict):
            _check(str(entry.get("degree", "")))
            _check(str(entry.get("institution", "")))

    checked_profile["skills"] = [
        skill for skill in profile.get("skills", []) if skill not in dropped
    ]
    checked_profile["languages"] = [
        language for language in profile.get("languages", []) if language not in dropped
    ]
    checked_profile["certifications"] = [
        cert for cert in profile.get("certifications", []) if cert not in dropped
    ]
    checked_profile["education"] = [
        entry
        for entry in profile.get("education", [])
        if isinstance(entry, dict)
        and entry.get("degree", "") not in dropped
        and entry.get("institution", "") not in dropped
    ]
    report = FidelityReport(
        dropped_facts=dropped,
        supported_facts=supported,
        cleaned_profile=checked_profile,
    )
    return report


class LLMProfileExtractor:
    """ProfileExtractor backed by the async LLM client at temperature 0.

    After each ``extract`` call, ``last_dropped_facts`` records the facts the
    fidelity checkpoint dropped (empty in strict mode, which retries or
    raises instead) so the orchestrator can surface them in ``meta``.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        settings: Settings,
        *,
        model: str | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._settings = settings
        self._model = model or settings.profile_model
        self._max_retries = 2
        self.last_dropped_facts: list[str] = []

    async def extract(self, markdown: str) -> dict[str, Any]:
        messages: list[Message] = [
            {"role": "system", "content": PROFILE_SYSTEM_PROMPT},
            {"role": "user", "content": build_profile_prompt(markdown)},
        ]
        attempts = 0
        max_attempts = self._max_retries + 1
        while True:
            attempts += 1
            try:
                data = await self._llm_client.complete(messages, schema=PROFILE_SCHEMA)
                profile = self._validate(data)
                report = check_fidelity(markdown, profile)
                if report.dropped_facts:
                    if self._settings.profile_fidelity == "strict":
                        raise LLMInvalidOutputError(
                            "The model produced facts not supported by the "
                            f"resume text: {report.dropped_facts}"
                        )
                    # Lenient: drop unsupported facts, log, continue.
                    logger.info(
                        "Fidelity check dropped unsupported facts: %s",
                        report.dropped_facts,
                    )
                    self.last_dropped_facts = report.dropped_facts
                    return report.cleaned_profile
                self.last_dropped_facts = []
                return profile
            except LLMInvalidOutputError:
                if attempts >= max_attempts:
                    raise
                logger.warning(
                    "Profile extraction invalid (attempt %s/%s); retrying",
                    attempts,
                    max_attempts,
                )
                messages = [
                    *messages,
                    {"role": "user", "content": _CORRECTIVE_PROMPT},
                ]
                continue

    def _validate(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise LLMInvalidOutputError("The model returned a non-object response.")
        try:
            model = ResumeProfile.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - normalize validation failures
            raise LLMInvalidOutputError(
                "The model returned a profile that does not match the schema."
            ) from exc
        return model.model_dump(mode="json")
