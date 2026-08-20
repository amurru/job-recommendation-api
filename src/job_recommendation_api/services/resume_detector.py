"""Cheap heuristic gate that decides whether converted text is resume-like.

This is a coarse pre-filter, not a classifier: it catches obvious non-resumes
(student portals, bank statements, random documents) before spending an LLM
call on them. Ambiguous documents pass through and the LLM remains the
authority.
"""

from __future__ import annotations

import re

# A bare email address is the strongest single resume signal.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

# Section headers / vocabulary strongly associated with resumes. A phone
# number is deliberately NOT a primary signal: passwords, IDs and reference
# numbers on random documents look like phone numbers.
_STRONG_SECTIONS: tuple[str, ...] = (
    "work experience",
    "professional experience",
    "employment history",
    "work history",
    "education",
    "skills",
    "projects",
    "experience",
)


def looks_like_resume(markdown: str) -> bool:
    """Best-effort gate: a contact address, or two resume-like sections."""
    text = (markdown or "").lower()
    if not text.strip():
        return False
    if _EMAIL_RE.search(text):
        return True
    strong_hits = sum(1 for section in _STRONG_SECTIONS if section in text)
    return strong_hits >= 2