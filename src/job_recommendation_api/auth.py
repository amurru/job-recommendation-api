"""API-key authentication: identity model and hashed key store (SH-001).

Keys arrive via ``API_KEYS`` (comma-separated env) or ``API_KEYS_FILE``
(one key per line, ``#`` comments). The store keeps SHA-256 digests only:
plaintext keys exist during construction and are never retained, logged, or
returned. Comparison is constant-time (``hmac.compare_digest``). The only
key-derived value safe for logs is the ``key_id`` (digest prefix).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from job_recommendation_api.config import Settings

logger = logging.getLogger(__name__)

IdentityKind = Literal["key", "anonymous"]

_KEY_ID_HEX_CHARS = 12


@dataclass(frozen=True)
class Identity:
    """Who is calling. ``key_id`` (digest prefix) is the only key-derived
    value; the plaintext key is never stored here."""

    kind: IdentityKind
    key_id: str | None = None
    ip: str | None = None


def _key_id_for(digest_hex: str) -> str:
    """Stable, log-safe identifier: first 12 hex chars of the SHA-256 digest."""
    return digest_hex[:_KEY_ID_HEX_CHARS]


def parse_keys_file(path: Path) -> list[str]:
    """Read one key per line; ``#`` starts a comment; blank lines ignored."""
    keys: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            keys.append(line)
    return keys


class ApiKeyStore:
    """In-memory store of SHA-256 key digests.

    Constructed once at startup from settings. ``verify`` returns the matching
    ``Identity`` or ``None``; the plaintext key is not retained after
    construction.
    """

    def __init__(self, keys: list[str]) -> None:
        # {sha256(key).hexdigest(): key_id} - digests only, never plaintext.
        self._digests: dict[bytes, str] = {}
        seen: set[str] = set()
        for key in keys:
            stripped = key.strip()
            if not stripped:
                continue
            digest = hashlib.sha256(stripped.encode("utf-8")).digest()
            digest_hex = digest.hex()
            if digest_hex in seen:
                logger.warning("Duplicate API key supplied; ignoring the repeat.")
                continue
            seen.add(digest_hex)
            self._digests[digest] = _key_id_for(digest_hex)

    def __len__(self) -> int:
        return len(self._digests)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"ApiKeyStore(keys={len(self._digests)} digests)"

    def verify(self, key: str) -> Identity | None:
        """Resolve a presented plaintext key to its ``Identity``.

        Constant-time digest comparison; ``None`` for unknown keys.
        """
        digest = hashlib.sha256(key.strip().encode("utf-8")).digest()
        for stored_digest, key_id in self._digests.items():
            if hmac.compare_digest(digest, stored_digest):
                return Identity(kind="key", key_id=key_id)
        return None

    @classmethod
    def from_settings(cls, settings: Settings) -> ApiKeyStore:
        keys = [key for key in settings.api_keys.split(",") if key.strip()]
        if settings.api_keys_file is not None:
            keys.extend(parse_keys_file(settings.api_keys_file))
        return cls(keys)
