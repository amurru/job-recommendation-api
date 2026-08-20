"""Deterministic injection guard for untrusted resume content.

The guard strips lines matching known instruction-override phrases before the
content reaches either LLM stage. It is a cheap layer, not a guarantee: the
untrusted-data instruction in the system prompts is the primary defense.

Bare generic words ("override", "disregard", "instructions") are deliberately
NOT patterns: they strip legitimate resume lines (e.g. a sales-comp
"commission overrides" bullet), and once cached the corruption is silent.
Lower recall against paraphrased injections is accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Multi-word instruction-override phrases only (case-insensitive).
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore\s+previous\s+instructions",
        r"ignore\s+all\s+previous",
        r"ignore\s+the\s+above",
        r"disregard\s+previous",
        r"disregard\s+the\s+above",
        r"disregard\s+all\s+previous",
        r"system\s+prompt",
        r"you\s+are\s+now",
        r"forget\s+everything",
        r"new\s+instructions",
        r"override\s+your\s+instructions",
        r"override\s+previous\s+instructions",
    )
)


@dataclass(frozen=True)
class GuardResult:
    """Outcome of guarding a text block."""

    cleaned_text: str
    removed_lines: int


class InjectionGuard:
    """Removes lines containing known injection phrases and counts them."""

    def __init__(self, patterns: tuple[re.Pattern[str], ...] = _INJECTION_PATTERNS) -> None:
        self._patterns = patterns

    def guard(self, text: str) -> GuardResult:
        """Return the text with matching lines removed and the removal count."""
        kept: list[str] = []
        removed = 0
        for line in text.splitlines(keepends=False):
            if any(pattern.search(line) for pattern in self._patterns):
                removed += 1
            else:
                kept.append(line)
        return GuardResult(cleaned_text="\n".join(kept), removed_lines=removed)
