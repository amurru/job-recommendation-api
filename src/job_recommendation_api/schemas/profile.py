"""Structured resume profile extracted once, validated, cached, and embedded
in the recommendation prompt.

``ResumeProfile`` is the single contract for the profile LLM stage: the model
emits it via its JSON Schema and the profiler re-validates it with Pydantic
before any fact is trusted (``PROFILE_SCHEMA`` in ``services/prompts.py``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

JSONObject = dict[str, object]


class EducationEntry(BaseModel):
    """A single education record extracted verbatim from the resume."""

    model_config = ConfigDict(extra="forbid")

    degree: str = Field(min_length=1)
    institution: str = Field(min_length=1)
    year: int | None = None


class ResumeProfile(BaseModel):
    """Facts extracted from the resume markdown.

    Discrete list facts (skills, education, languages, certifications) are the
    target of the fidelity checkpoint; ``summary``, ``current_title``,
    ``location``, and ``years_experience`` are trusted prose/derived fields.
    """

    model_config = ConfigDict(extra="forbid")

    current_title: str | None = None
    target_roles: list[str] = Field(default_factory=list, max_length=10)
    years_experience: float | None = None
    skills: list[str] = Field(default_factory=list, max_length=50)
    education: list[EducationEntry] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list, max_length=20)
    certifications: list[str] = Field(default_factory=list, max_length=20)
    location: str | None = None
    summary: str = Field(min_length=1, max_length=1000)


# The schema sent to the LLM (ResumeProfile only; no runtime wrapper).
PROFILE_SCHEMA: JSONObject = ResumeProfile.model_json_schema()
