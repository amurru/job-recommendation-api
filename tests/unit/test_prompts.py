"""ID-007: prompt building and schema tests."""

from __future__ import annotations

from job_recommendation_api.services.prompts import (
    MAX_RESUME_CHARS,
    RECOMMENDATION_SCHEMA,
    SYSTEM_PROMPT,
    build_user_prompt,
)


def test_build_user_prompt_embeds_resume() -> None:
    prompt = build_user_prompt("# Jane Doe\nPython developer")
    assert "<resume>" in prompt
    assert "</resume>" in prompt
    assert "# Jane Doe\nPython developer" in prompt


def test_build_user_prompt_truncates_long_resume() -> None:
    long_resume = "x" * (MAX_RESUME_CHARS + 100)
    prompt = build_user_prompt(long_resume)
    assert "...[resume truncated]..." in prompt


def test_system_prompt_mentions_json_rules() -> None:
    assert "ONLY valid JSON" in SYSTEM_PROMPT
    assert "fit_score" in SYSTEM_PROMPT


def test_recommendation_schema_has_expected_required_keys() -> None:
    required = set(RECOMMENDATION_SCHEMA["required"])
    assert required == {"summary", "top_skills", "jobs", "education_materials"}
