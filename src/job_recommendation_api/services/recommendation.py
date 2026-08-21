"""Recommendation orchestration: cache -> convert -> guard -> profile ->
prompt assembly -> LLM -> validate.

Extraction (markdown + profile) is cached by the versioned document hash;
the recommendation LLM call is deliberately NEVER cached (it is per-query).

The sync conversion stage (SH-006) runs under an ``anyio.CapacityLimiter``
so slow conversions cannot starve the threadpool, and (SH-008) under a
wall-clock deadline: expiry cancels the conversion task and releases the
limiter token. The limiter/deadline scope ONLY the sync conversion - never
the async LLM calls (those are bounded by rate limits + provider timeouts).
"""

from __future__ import annotations

import logging
from typing import Any

import anyio
from anyio.to_thread import run_sync

from job_recommendation_api.errors import (
    DocumentConversionError,
    LLMInvalidOutputError,
    NotAResumeError,
)
from job_recommendation_api.llm.client import LLMClient
from job_recommendation_api.schemas.recommendation import (
    RecommendationResponse,
    ResumeAnalysis,
)
from job_recommendation_api.services.document_converter import (
    EXTRACTION_VERSION,
    DocumentConverter,
    cache_key,
)
from job_recommendation_api.services.extraction_cache import (
    CachedExtraction,
    ExtractionCache,
)
from job_recommendation_api.services.injection_guard import InjectionGuard
from job_recommendation_api.services.prompts import (
    MAX_RESUME_CHARS,
    RECOMMENDATION_SCHEMA,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from job_recommendation_api.services.resume_detector import looks_like_resume
from job_recommendation_api.services.resume_profiler import ProfileExtractor

logger = logging.getLogger(__name__)

Message = dict[str, str]

_CORRECTIVE_PROMPT = (
    "Your previous response was invalid. Return ONLY a single JSON object that "
    "matches the expected output shape shown in the resume message exactly: "
    "no markdown fences, no commentary, no extra keys, no empty values where "
    "a string is required."
)

_DEFAULT_CONVERT_DEADLINE_SECONDS = 30.0


def _convert(converter: DocumentConverter, document_bytes: bytes, name: str) -> str:
    """Callable helper so the sync converter can run in a threadpool."""
    return converter.convert(document_bytes, name=name)


class RecommendationService:
    """Single business operation turning document bytes into validated guidance.

    Async throughout: the sync markitdown converter runs in a threadpool; the
    profile and recommendation LLM calls are async-native. No FastAPI/HTTP
    imports here.
    """

    def __init__(
        self,
        converter: DocumentConverter,
        llm_client: LLMClient,
        *,
        model: str,
        profiler: ProfileExtractor,
        extraction_cache: ExtractionCache,
        injection_guard: InjectionGuard | None = None,
        convert_limiter: anyio.CapacityLimiter | None = None,
        convert_deadline_seconds: float = _DEFAULT_CONVERT_DEADLINE_SECONDS,
    ) -> None:
        self._converter = converter
        self._llm_client = llm_client
        self._model = model
        self._profiler = profiler
        self._cache = extraction_cache
        self._guard = injection_guard or InjectionGuard()
        self.convert_limiter = convert_limiter
        self._convert_deadline_seconds = convert_deadline_seconds

    async def recommend(self, document_bytes: bytes, *, name: str) -> RecommendationResponse:
        key = cache_key(document_bytes)
        cached = self._cache.get(key)
        if cached is not None:
            logger.info("Extraction cache HIT (key=%s...)", key[:12])
            markdown = cached.markdown
            profile = cached.profile
            dropped_facts = cached.dropped_facts
            injection_lines_removed = cached.injection_lines_removed
            cache_state: str = "hit"
        else:
            cache_state = "miss"
            markdown = await self._convert_document(document_bytes, name=name)
            guard_result = self._guard.guard(markdown)
            markdown = guard_result.cleaned_text
            injection_lines_removed = guard_result.removed_lines
            profile = await self._profiler.extract(markdown)
            dropped_facts = list(getattr(self._profiler, "last_dropped_facts", []))
            self._cache.set(
                key,
                CachedExtraction(
                    markdown=markdown,
                    profile=profile,
                    dropped_facts=dropped_facts,
                    injection_lines_removed=injection_lines_removed,
                    ocr_used=self._ocr_used(),
                    converter_version=EXTRACTION_VERSION,
                ),
            )

        if not looks_like_resume(markdown):
            raise NotAResumeError(
                "The uploaded document does not appear to be a resume. Please upload a PDF resume."
            )

        snapshot = self._snapshot(markdown)
        messages: list[Message] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(markdown, profile)},
        ]
        data = await self._llm_client.complete(messages, schema=RECOMMENDATION_SCHEMA)
        if not isinstance(data, dict):
            raise LLMInvalidOutputError("The model returned a non-object response.")
        try:
            analysis = self._validate(data)
        except LLMInvalidOutputError:
            corrective = [
                *messages,
                {"role": "user", "content": _CORRECTIVE_PROMPT},
            ]
            data = await self._llm_client.complete(corrective, schema=RECOMMENDATION_SCHEMA)
            analysis = self._validate(data)

        payload: dict[str, Any] = {
            "analysis": analysis,
            "meta": {
                "model": self._model,
                # Honest: the length of the snapshot actually embedded.
                "markdown_length": len(snapshot),
                "cache": cache_state,
                "markdown_truncated": len(markdown) > MAX_RESUME_CHARS,
                "dropped_facts": dropped_facts,
                "injection_lines_removed": injection_lines_removed,
            },
        }
        return RecommendationResponse.model_validate(payload)

    async def _convert_document(self, document_bytes: bytes, *, name: str) -> str:
        """Run the sync conversion under the concurrency limiter (SH-006) and
        the wall-clock deadline (SH-008).

        The deadline covers limiter wait + conversion. On expiry the caller
        gets ``DocumentConversionError`` immediately and the limiter token is
        released by the exiting ``async with`` scope; the worker thread itself
        is abandoned at the next await point (threads cannot be killed - the
        deadline bounds request latency and pool occupancy, not CPU).
        Converter errors (invalid document, structural caps) propagate
        unchanged.
        """
        if self.convert_limiter is None:
            return await run_sync(_convert, self._converter, document_bytes, name)

        result: str | None = None
        with anyio.move_on_after(self._convert_deadline_seconds) as scope:
            async with self.convert_limiter:
                result = await run_sync(
                    _convert, self._converter, document_bytes, name, abandon_on_cancel=True
                )
        if scope.cancelled_caught or result is None:
            logger.warning(
                "Conversion deadline exceeded (%.1fs) for %s",
                self._convert_deadline_seconds,
                name,
            )
            raise DocumentConversionError(
                "The document conversion timed out. The document may be too complex to process."
            )
        return result

    def _snapshot(self, markdown: str) -> str:
        snapshot = markdown[:MAX_RESUME_CHARS]
        if len(markdown) > MAX_RESUME_CHARS:
            snapshot += "\n...[resume truncated]..."
        return snapshot

    def _ocr_used(self) -> bool:
        return getattr(self._converter, "_ocr_client", None) is not None

    def _validate(self, data: dict[str, Any]) -> dict[str, Any]:
        try:
            model = ResumeAnalysis.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - normalize validation failures
            raise LLMInvalidOutputError(
                "The model returned data that does not match the schema."
            ) from exc
        return model.model_dump(mode="json")
