"""FP-003: ResumeProfile schema validation tests."""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import ValidationError

from job_recommendation_api.schemas.profile import PROFILE_SCHEMA, ResumeProfile


def test_valid_minimal_profile() -> None:
    profile = ResumeProfile.model_validate({"summary": "Backend engineer."})
    assert profile.summary == "Backend engineer."
    assert profile.skills == []
    assert profile.current_title is None
    assert profile.years_experience is None


def test_full_profile_round_trip() -> None:
    data = {
        "current_title": "Senior Backend Engineer",
        "target_roles": ["Staff Engineer"],
        "years_experience": 8.5,
        "skills": ["Python", "FastAPI"],
        "education": [{"degree": "BSc CS", "institution": "MIT", "year": 2015}],
        "languages": ["English"],
        "certifications": ["AWS SA"],
        "location": "Berlin",
        "summary": "Experienced engineer.",
    }
    profile = ResumeProfile.model_validate(data)
    assert profile.model_dump(mode="json") == data


def test_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        ResumeProfile.model_validate({"summary": "x", "hacker_field": "nope"})


def test_rejects_missing_summary() -> None:
    with pytest.raises(ValidationError):
        ResumeProfile.model_validate({"skills": ["Python"]})


def test_rejects_empty_summary() -> None:
    with pytest.raises(ValidationError):
        ResumeProfile.model_validate({"summary": ""})


def test_rejects_overlong_summary() -> None:
    with pytest.raises(ValidationError):
        ResumeProfile.model_validate({"summary": "x" * 1001})


def test_rejects_oversized_skills_list() -> None:
    with pytest.raises(ValidationError):
        ResumeProfile.model_validate({"summary": "x", "skills": [f"s{i}" for i in range(51)]})


def test_rejects_oversized_target_roles() -> None:
    with pytest.raises(ValidationError):
        ResumeProfile.model_validate({"summary": "x", "target_roles": [f"r{i}" for i in range(11)]})


def test_education_entry_requires_degree_and_institution() -> None:
    with pytest.raises(ValidationError):
        ResumeProfile.model_validate({"summary": "x", "education": [{"degree": "BSc"}]})


def test_education_year_optional() -> None:
    profile = ResumeProfile.model_validate(
        {"summary": "x", "education": [{"degree": "BSc", "institution": "MIT"}]}
    )
    assert profile.education[0].year is None


def test_profile_schema_exported() -> None:
    assert PROFILE_SCHEMA["type"] == "object"
    properties = cast("dict[str, object]", PROFILE_SCHEMA["properties"])
    assert set(properties) == {
        "current_title",
        "target_roles",
        "years_experience",
        "skills",
        "education",
        "languages",
        "certifications",
        "location",
        "summary",
    }
