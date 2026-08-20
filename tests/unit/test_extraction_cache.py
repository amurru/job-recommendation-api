"""FP-006: in-process extraction cache tests (TTL, LRU, thread-safety)."""

from __future__ import annotations

import threading
import time

from job_recommendation_api.services.extraction_cache import (
    CachedExtraction,
    InMemoryExtractionCache,
)


def _entry(markdown: str = "# Jane") -> CachedExtraction:
    return CachedExtraction(
        markdown=markdown,
        profile={"summary": "Engineer.", "skills": ["Python"]},
        dropped_facts=[],
        injection_lines_removed=0,
    )


def test_set_then_get_round_trip() -> None:
    cache = InMemoryExtractionCache(max_entries=4, ttl_seconds=60)
    cache.set("k1", _entry())
    value = cache.get("k1")
    assert value is not None
    assert value.markdown == "# Jane"


def test_missing_key_returns_none() -> None:
    cache = InMemoryExtractionCache(max_entries=4, ttl_seconds=60)
    assert cache.get("missing") is None


def test_ttl_expiry_lazy_on_access() -> None:
    cache = InMemoryExtractionCache(max_entries=4, ttl_seconds=1)
    cache.set("k1", _entry())
    # Simulate age without sleeping: backdate the entry.
    cached_at, value = cache._entries["k1"]
    cache._entries["k1"] = (cached_at - 2, value)
    assert cache.get("k1") is None


def test_lru_eviction_at_max_entries() -> None:
    cache = InMemoryExtractionCache(max_entries=2, ttl_seconds=60)
    cache.set("a", _entry("a"))
    cache.set("b", _entry("b"))
    cache.get("a")  # touch a -> b becomes LRU
    cache.set("c", _entry("c"))
    assert cache.get("a") is not None
    assert cache.get("b") is None
    assert cache.get("c") is not None


def test_set_evicts_oldest_without_touch() -> None:
    cache = InMemoryExtractionCache(max_entries=2, ttl_seconds=60)
    cache.set("a", _entry("a"))
    cache.set("b", _entry("b"))
    cache.set("c", _entry("c"))
    assert cache.get("a") is None


def test_key_includes_extraction_version() -> None:
    from job_recommendation_api.services.document_converter import (
        EXTRACTION_VERSION,
        cache_key,
    )

    key = cache_key(b"document-bytes")
    assert key.endswith(f":v{EXTRACTION_VERSION}")
    assert key == cache_key(b"document-bytes")  # deterministic
    assert key != cache_key(b"other-bytes")


def test_concurrent_get_set_do_not_corrupt() -> None:
    cache = InMemoryExtractionCache(max_entries=64, ttl_seconds=60)
    errors: list[Exception] = []

    def worker(index: int) -> None:
        try:
            for i in range(50):
                key = f"k{index % 8}"
                cache.set(key, _entry(f"{index}-{i}"))
                cache.get(key)
        except Exception as exc:  # noqa: BLE001 - collect for assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert errors == []
    assert len(cache._entries) <= 8


def test_cached_at_preserved_on_set() -> None:
    cache = InMemoryExtractionCache(max_entries=4, ttl_seconds=60)
    entry = _entry()
    entry.cached_at = time.time() - 10
    cache.set("k", entry)
    value = cache.get("k")
    assert value is not None
    assert abs(value.cached_at - (time.time() - 10)) < 1


def test_counts_cached_for_hit_miss_parity() -> None:
    cache = InMemoryExtractionCache(max_entries=4, ttl_seconds=60)
    entry = CachedExtraction(
        markdown="# Jane",
        profile={"summary": "x"},
        dropped_facts=["FabricatedSkill"],
        injection_lines_removed=2,
    )
    cache.set("k", entry)
    value = cache.get("k")
    assert value is not None
    assert value.dropped_facts == ["FabricatedSkill"]
    assert value.injection_lines_removed == 2
    assert time.time() - value.cached_at < 60
