"""ID-003: schema model and JSON Schema export tests."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from job_recommendation_api.schemas.recommendation import (
    RESUME_ANALYSIS_SCHEMA,
    JobRecommendation,
    LearningResource,
    RecommendationResponse,
    ResponseMeta,
    ResumeAnalysis,
)


def _valid_analysis() -> dict[str, Any]:
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


def test_valid_analysis_roundtrip() -> None:
    analysis = ResumeAnalysis.model_validate(_valid_analysis())
    assert analysis.jobs[0].fit_score == 0.9


@pytest.mark.parametrize(
    "field,value",
    [
        ("fit_score", 1.5),
        ("fit_score", -0.1),
    ],
)
def test_rejects_out_of_range_fit_score(field: str, value: float) -> None:
    data = _valid_analysis()
    data["jobs"][0][field] = value
    with pytest.raises(ValidationError):
        ResumeAnalysis.model_validate(data)


def test_rejects_invalid_seniority_level() -> None:
    data = _valid_analysis()
    data["jobs"][0]["seniority_level"] = "lead"
    with pytest.raises(ValidationError):
        ResumeAnalysis.model_validate(data)


def test_rejects_invalid_kind() -> None:
    data = _valid_analysis()
    data["education_materials"][0]["kind"] = "video"
    with pytest.raises(ValidationError):
        ResumeAnalysis.model_validate(data)


def test_learning_resource_optional_fields_default_none() -> None:
    resource = LearningResource(
        topic="t",
        kind="book",
        title="title",
        rationale="r",
    )
    assert resource.provider is None
    assert resource.url is None


def test_rejects_unknown_fields() -> None:
    data = _valid_analysis()
    data["unexpected"] = "x"
    with pytest.raises(ValidationError):
        ResumeAnalysis.model_validate(data)


def test_resume_analysis_schema_omits_optional_fields_from_required() -> None:
    schema = RESUME_ANALYSIS_SCHEMA
    assert schema["type"] == "object"
    top_required = set(schema["required"])
    assert top_required == {"summary", "top_skills", "jobs", "education_materials"}

    edu_required = schema["$defs"]["LearningResource"]["required"]
    assert "provider" not in edu_required
    assert "url" not in edu_required


def test_recommendation_response_includes_meta() -> None:
    analysis = ResumeAnalysis.model_validate(_valid_analysis())
    response = RecommendationResponse(
        analysis=analysis,
        meta=ResponseMeta(model="openai/gpt-4o-mini", markdown_length=10),
    )
    assert response.meta is not None
    assert response.meta.model == "openai/gpt-4o-mini"
    assert response.meta.markdown_length == 10


def test_job_recommendation_key_skills_max_len() -> None:
    with pytest.raises(ValidationError):
        JobRecommendation(
            title="t",
            fit_score=0.5,
            seniority_level="mid",
            rationale="r",
            key_skills=["s"] * 21,
        )
