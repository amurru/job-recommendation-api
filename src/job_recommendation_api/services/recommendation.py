"""Recommendation orchestration: converter -> prompt assembly -> LLM -> validate."""

from __future__ import annotations

from typing import Any

from anyio.to_thread import run_sync

from job_recommendation_api.errors import LLMInvalidOutputError, NotAResumeError
from job_recommendation_api.llm.client import LLMClient
from job_recommendation_api.schemas.recommendation import (
    RecommendationResponse,
    ResumeAnalysis,
)
from job_recommendation_api.services.document_converter import DocumentConverter
from job_recommendation_api.services.prompts import (
    RECOMMENDATION_SCHEMA,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from job_recommendation_api.services.resume_detector import looks_like_resume

Message = dict[str, str]

_CORRECTIVE_PROMPT = (
    "Your previous response was invalid. Return ONLY a single JSON object that "
    "matches the expected output shape shown in the resume message exactly: "
    "no markdown fences, no commentary, no extra keys, no empty values where "
    "a string is required."
)


def _convert(converter: DocumentConverter, document_bytes: bytes, name: str) -> str:
    """Callable helper so the sync converter can run in a threadpool."""
    return converter.convert(document_bytes, name=name)


class RecommendationService:
    """Single business operation turning document bytes into validated guidance.

    Async throughout: the sync markitdown converter runs in a threadpool; the
    LLM call is async-native. No FastAPI/HTTP imports here.
    """

    def __init__(
        self,
        converter: DocumentConverter,
        llm_client: LLMClient,
        *,
        model: str,
    ) -> None:
        self._converter = converter
        self._llm_client = llm_client
        self._model = model

    async def recommend(self, document_bytes: bytes, *, name: str) -> RecommendationResponse:
        markdown = await run_sync(_convert, self._converter, document_bytes, name)
        if not looks_like_resume(markdown):
            raise NotAResumeError(
                "The uploaded document does not appear to be a resume. Please upload a PDF resume."
            )
        messages: list[Message] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(markdown)},
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
                "markdown_length": len(markdown),
            },
        }
        return RecommendationResponse.model_validate(payload)

    def _validate(self, data: dict[str, Any]) -> dict[str, Any]:
        try:
            model = ResumeAnalysis.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - normalize validation failures
            raise LLMInvalidOutputError(
                "The model returned data that does not match the schema."
            ) from exc
        return model.model_dump(mode="json")
