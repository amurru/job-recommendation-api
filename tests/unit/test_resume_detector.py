"""Unit tests for the resume-likeness heuristic gate."""

from __future__ import annotations

import pytest

from job_recommendation_api.services.resume_detector import looks_like_resume


@pytest.mark.parametrize(
    "markdown",
    [
        # Email address alone is enough.
        "# Jane Doe\nPython developer\njane@example.com",
        "Contact: jane.doe+work@company.io",
        # Two resume-like sections without contact info.
        "# Jane\nWork Experience: backend engineer 2018-2024\nEducation: BSc CS",
        "# Resume\nSkills: Python\nProjects: API gateway\nExperience: 6 years",
        # Case-insensitive section matching.
        "EDUCATION\nUniversity of X\nSKILLS\nPython",
    ],
)
def test_looks_like_resume_accepts(markdown: str) -> None:
    assert looks_like_resume(markdown) is True


@pytest.mark.parametrize(
    "markdown",
    [
        # Student portal / credentials page: ID + password look like numbers,
        # but there is no email and no resume section vocabulary.
        "Student Information\nStudent USER ID: ammar_94038\nStudent Password: 06100059564",
        # Unrelated document.
        "# Quarterly Financial Report\nRevenue grew 12% in Q3.",
        # Single generic line is not enough.
        "Python backend engineer",
        "",
        "   \n  ",
    ],
)
def test_looks_like_resume_rejects(markdown: str) -> None:
    assert looks_like_resume(markdown) is False