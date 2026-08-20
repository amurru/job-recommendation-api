"""Prompt templates and the LLM structured-output JSON Schema.

The schema is derived from ``ResumeAnalysis`` (never hand-maintained) and is
the single contract both the LLM output and the HTTP response are validated
against.
"""

from __future__ import annotations

import json
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

# A concrete example of the expected output. Models follow examples far more
# reliably than a raw JSON Schema, and this is the only schema the model sees
# when the provider rejects ``json_schema`` output and the client falls back to
# ``json_object`` mode. Keep this in sync with ``ResumeAnalysis``.
SCHEMA_EXAMPLE: str = json.dumps(
    {
        "summary": "Two-sentence summary of the candidate.",
        "top_skills": ["skill-1", "skill-2"],
        "jobs": [
            {
                "title": "Recommended job title",
                "fit_score": 0.9,
                "seniority_level": "senior",
                "rationale": "One-sentence rationale.",
                "key_skills": ["skill-1", "skill-2"],
            }
        ],
        "education_materials": [
            {
                "topic": "Learning topic",
                "kind": "book",
                "title": "Resource title",
                "provider": "Provider",
                "url": "https://example.com",
                "rationale": "One-sentence rationale.",
            }
        ],
    },
    indent=2,
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
        "Return your analysis as JSON conforming to the supplied schema.\n"
        "\n"
        "Expected output shape (follow this exactly, but use real content from "
        "the resume):\n"
        f"{SCHEMA_EXAMPLE}"
    )


# The schema the model must emit: ResumeAnalysis only (meta is runtime-only).
RECOMMENDATION_SCHEMA: JSONObject = ResumeAnalysis.model_json_schema()
