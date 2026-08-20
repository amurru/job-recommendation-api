"""Hash-based extraction cache: document bytes -> {markdown, profile, counts}.

Extraction only - the recommendation LLM call is deliberately never cached
(it is per-query). Storage is in-process, TTL-bounded, LRU-capped, and
thread-safe behind the ``ExtractionCache`` Protocol so a shared store (Redis)
can replace it later without call-site changes.

Fidelity/injection counts are cached alongside markdown and profile so a HIT
reports the same ``meta`` as the MISS that populated it.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class CachedExtraction:
    """Everything the recommendation stage needs from the extraction stage."""

    markdown: str
    profile: dict[str, Any]
    dropped_facts: list[str] = field(default_factory=list)
    injection_lines_removed: int = 0
    ocr_used: bool = False
    converter_version: str = ""
    cached_at: float = field(default_factory=time.time)


class ExtractionCache(Protocol):
    """Bounded cache of extraction output keyed by a versioned doc hash."""

    def get(self, key: str) -> CachedExtraction | None: ...

    def set(self, key: str, value: CachedExtraction) -> None: ...


class InMemoryExtractionCache:
    """Thread-safe dict with LRU eviction and lazy TTL expiry.

    The lock is held only for dict operations (microseconds); it is called
    from both the converter threadpool and the async orchestration path.
    """

    def __init__(self, *, max_entries: int = 256, ttl_seconds: int = 3600) -> None:
        self._max_entries = max(1, max_entries)
        self._ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, tuple[float, CachedExtraction]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> CachedExtraction | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            cached_at, value = entry
            if time.time() - cached_at > self._ttl_seconds:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return value

    def set(self, key: str, value: CachedExtraction) -> None:
        with self._lock:
            self._entries[key] = (value.cached_at, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
