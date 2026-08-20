"""FP-004: deterministic fidelity checkpoint tests."""

from __future__ import annotations

from job_recommendation_api.services.resume_profiler import check_fidelity

_MARKDOWN = """
# Jane Doe
Skills: Python, FastAPI, PostgreSQL
Education: BSc Computer Science, MIT
Languages: English, German
Certifications: AWS Solutions Architect
"""


def test_supported_facts_survive() -> None:
    profile = {
        "summary": "Engineer.",
        "skills": ["Python", "FastAPI"],
        "education": [{"degree": "BSc Computer Science", "institution": "MIT"}],
        "languages": ["English"],
        "certifications": ["AWS Solutions Architect"],
    }
    report = check_fidelity(_MARKDOWN, profile)
    assert report.dropped_facts == []
    assert report.supported_facts == 6
    assert report.cleaned_profile["skills"] == ["Python", "FastAPI"]


def test_fabricated_skill_dropped() -> None:
    profile = {
        "summary": "Engineer.",
        "skills": ["Python", "QuantumBlockchainNinja"],
    }
    report = check_fidelity(_MARKDOWN, profile)
    assert report.dropped_facts == ["QuantumBlockchainNinja"]
    assert report.cleaned_profile["skills"] == ["Python"]


def test_paraphrased_but_supported_fact_survives_token_rule() -> None:
    # Not a substring, but every significant token appears in the markdown.
    profile = {
        "summary": "Engineer.",
        "skills": ["PostgreSQL Python"],
    }
    report = check_fidelity(_MARKDOWN, profile)
    assert report.dropped_facts == []
    assert report.cleaned_profile["skills"] == ["PostgreSQL Python"]


def test_case_and_whitespace_normalized() -> None:
    profile = {
        "summary": "Engineer.",
        "skills": ["  PYTHON  "],
    }
    report = check_fidelity(_MARKDOWN, profile)
    assert report.dropped_facts == []


def test_short_tokens_ignored_in_token_rule() -> None:
    # "Go" has no significant token (length <= 2) and is not a substring.
    profile = {"summary": "Engineer.", "skills": ["Go"]}
    report = check_fidelity(_MARKDOWN, profile)
    assert report.dropped_facts == ["Go"]


def test_trusted_fields_not_checked() -> None:
    profile = {
        "summary": "Completely invented prose summary.",
        "current_title": "Invented Title",
        "location": "Atlantis",
        "years_experience": 99.0,
        "skills": [],
    }
    report = check_fidelity(_MARKDOWN, profile)
    assert report.dropped_facts == []


def test_empty_profile_passes() -> None:
    report = check_fidelity(_MARKDOWN, {"summary": "Engineer."})
    assert report.dropped_facts == []
    assert report.supported_facts == 0


def test_unsupported_education_dropped() -> None:
    profile = {
        "summary": "Engineer.",
        "education": [
            {"degree": "BSc Computer Science", "institution": "MIT"},
            {"degree": "PhD Alchemy", "institution": "Hogwarts"},
        ],
    }
    report = check_fidelity(_MARKDOWN, profile)
    assert "PhD Alchemy" in report.dropped_facts
    assert "Hogwarts" in report.dropped_facts
    assert len(report.cleaned_profile["education"]) == 1
