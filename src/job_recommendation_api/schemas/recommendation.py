"""Pydantic response models and JSON Schema exports.

These are the single source of truth for the response shape and, via
``ResumeAnalysis.model_json_schema()``, for the LLM's structured-output
contract (``RESUME_ANALYSIS_SCHEMA`` in ``services/prompts.py``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

JSONObject = dict[str, Any]

SeniorityLevel = Literal[
    "intern",
    "entry",
    "junior",
    "mid",
    "senior",
    "staff",
    "principal",
    "executive",
]

LearningKind = Literal["course", "book", "certification", "tutorial", "project"]


class JobRecommendation(BaseModel):
    """A single recommended role with a fit score and rationale."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    fit_score: float = Field(ge=0.0, le=1.0)
    seniority_level: SeniorityLevel
    rationale: str = Field(min_length=1)
    key_skills: list[str] = Field(default_factory=list, max_length=20)


class LearningResource(BaseModel):
    """A course/book/... recommended to close a skills gap."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1)
    kind: LearningKind
    title: str = Field(min_length=1)
    provider: str | None = None
    url: HttpUrl | None = None
    rationale: str = Field(min_length=1)


class ResumeAnalysis(BaseModel):
    """The LLM-produced analysis. Sent to the model via its JSON Schema."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=1000)
    top_skills: list[str] = Field(min_length=1, max_length=20)
    jobs: list[JobRecommendation]
    education_materials: list[LearningResource]


class ResponseMeta(BaseModel):
    """Runtime-only metadata attached server-side (not part of the LLM schema).

    All fields beyond ``model`` are additive with safe defaults so existing
    responses remain valid.
    """

    model_config = ConfigDict(extra="forbid")

    model: str
    markdown_length: int | None = None
    cache: Literal["hit", "miss"] | None = None
    markdown_truncated: bool = False
    dropped_facts: list[str] = Field(default_factory=list)
    injection_lines_removed: int = 0


class RecommendationResponse(BaseModel):
    """The HTTP response envelope: analysis + runtime meta."""

    model_config = ConfigDict(extra="forbid")

    analysis: ResumeAnalysis
    meta: ResponseMeta


# The schema sent to the LLM (ResumeAnalysis only; `meta` is runtime-only).
RESUME_ANALYSIS_SCHEMA: JSONObject = ResumeAnalysis.model_json_schema()
