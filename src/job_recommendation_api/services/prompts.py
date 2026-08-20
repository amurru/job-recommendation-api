"""Prompt templates and the LLM structured-output JSON Schema.

The schema is derived from ``ResumeAnalysis`` (never hand-maintained) and is
the single contract both the LLM output and the HTTP response are validated
against.
"""

from __future__ import annotations

from typing import Any

from job_recommendation_api.schemas.recommendation import ResumeAnalysis

JSONObject = dict[str, Any]

SYSTEM_PROMPT = (
    "You are a career advisory assistant. Given the text of a candidate's "
    "resume (markdown), analyze it and produce a single JSON object that "
    "matches the provided JSON schema exactly.\n"
    "\n"
    "Rules:\n"
    "- Output ONLY valid JSON, no markdown fences, no commentary, no extra keys.\n"
    '- Rank "jobs" by fit (highest first) and give each a rationale and matching skills.\n'
    '- Recommend "education_materials" that close gaps relevant to the recommended jobs.\n'
    "- fit_score is a float between 0.0 and 1.0.\n"
    "- Keep values concise and professional."
)

# Max resume characters embedded in the user prompt, to guard the token budget.
MAX_RESUME_CHARS = 20_000


def build_user_prompt(resume_markdown: str) -> str:
    """Build the user message embedding a length-capped resume snapshot."""
    snapshot = resume_markdown[:MAX_RESUME_CHARS]
    if len(resume_markdown) > MAX_RESUME_CHARS:
        snapshot += "\n...[resume truncated]..."
    return (
        "Here is the resume (markdown):\n"
        "\n"
        "<resume>\n"
        f"{snapshot}\n"
        "</resume>\n"
        "\n"
        "Return your analysis as JSON conforming to the supplied schema."
    )


# The schema the model must emit: ResumeAnalysis only (meta is runtime-only).
RECOMMENDATION_SCHEMA: JSONObject = ResumeAnalysis.model_json_schema()
