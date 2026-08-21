"""Per-identity sliding-window rate limiting (SH-004).

``RateLimiter`` is a Protocol seam: the in-process ``SlidingWindowRateLimiter``
is correct for the default single-worker deployment (SH-ADR-003); a shared
(Redis) implementation can replace it later without call-site changes.

State is bounded: at most ``max_tracked_identities`` identity windows are
kept, evicting the least-recently-used identity when the bound is hit. A
sufficiently large botnet can evict rivals' windows (bounded harm: one
window's budget) so anonymous IP flooding cannot grow memory unboundedly.

The clock is injectable for deterministic tests; no wall-clock sleeps exist
in the check path.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from job_recommendation_api.auth import Identity, IdentityKind


@dataclass(frozen=True)
class WindowLimit:
    """Request budget for one identity tier."""

    requests: int
    window_seconds: float


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of one limiter check; feeds the ``X-RateLimit-*`` headers."""

    allowed: bool
    limit: int
    remaining: int
    reset_epoch: float
    retry_after_seconds: int


class RateLimiter(Protocol):
    """Checks one request against the caller's per-identity budget."""

    def check(self, identity: Identity) -> RateLimitDecision: ...


def _identity_key(identity: Identity) -> str:
    """Stable state key per identity: key_id for keyed callers, IP for
    anonymous ones."""
    if identity.kind == "key":
        return f"key:{identity.key_id}"
    return f"anon:{identity.ip or 'unknown'}"


class SlidingWindowRateLimiter:
    """Thread-safe sliding-window limiter with one window per identity.

    ``check`` records the request when allowed and returns the decision with
    the header fields. Windows are pruned lazily on access; the tracked-
    identity map is LRU-bounded.
    """

    def __init__(
        self,
        limits: dict[IdentityKind, WindowLimit],
        *,
        max_tracked_identities: int = 10_000,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._limits = dict(limits)
        self._max_tracked = max_tracked_identities
        self._clock = clock
        self._lock = threading.Lock()
        # identity key -> OrderedDict of (timestamp, seq) -> None. The seq
        # tiebreaker keeps same-instant requests distinct (a coarse clock or
        # a burst must never collapse two requests into one window entry).
        # Insertion order = request order, so the oldest entries are first.
        self._windows: OrderedDict[str, OrderedDict[tuple[float, int], None]] = OrderedDict()
        # identity key -> last-seen clock value (for LRU eviction).
        self._last_seen: OrderedDict[str, float] = OrderedDict()
        self._seq = 0

    def _limit_for(self, identity: Identity) -> WindowLimit | None:
        return self._limits.get(identity.kind)

    def _prune(self, window: OrderedDict[tuple[float, int], None], now: float, span: float) -> None:
        """Drop entries older than the window (caller holds the lock)."""
        cutoff = now - span
        while window:
            oldest_key = next(iter(window))
            if oldest_key[0] > cutoff:
                break
            del window[oldest_key]

    def _evict_if_needed(self) -> None:
        """Drop the least-recently-seen identity when over capacity.

        Caller must hold the lock. Never evicts the identity currently being
        checked (it is refreshed to most-recent first).
        """
        while len(self._windows) > self._max_tracked:
            lru_key, _ = self._last_seen.popitem(last=False)
            if lru_key in self._windows:
                del self._windows[lru_key]

    def check(self, identity: Identity) -> RateLimitDecision:
        limit = self._limit_for(identity)
        if limit is None or limit.requests <= 0:
            # No configured budget for this tier: treat as unlimited.
            now = self._clock()
            return RateLimitDecision(
                allowed=True,
                limit=0,
                remaining=0,
                reset_epoch=now,
                retry_after_seconds=0,
            )

        key = _identity_key(identity)
        now = self._clock()

        with self._lock:
            window = self._windows.get(key)
            if window is None:
                window = OrderedDict()
                self._windows[key] = window
            self._prune(window, now, limit.window_seconds)
            if not window:
                # Fully expired window: drop the tracked identity entirely so
                # state shrinks with inactivity, not just with eviction.
                del self._windows[key]
                self._last_seen.pop(key, None)
                window = OrderedDict()
                self._windows[key] = window

            if len(window) >= limit.requests:
                # Denied: the oldest request in the window defines the reset.
                oldest = next(iter(window))
                reset_epoch = oldest[0] + limit.window_seconds
                retry_after = max(1, int(reset_epoch - now) + 1)
                self._last_seen[key] = now
                self._last_seen.move_to_end(key)
                self._evict_if_needed()
                return RateLimitDecision(
                    allowed=False,
                    limit=limit.requests,
                    remaining=0,
                    reset_epoch=reset_epoch,
                    retry_after_seconds=retry_after,
                )

            self._seq += 1
            window[(now, self._seq)] = None
            self._last_seen[key] = now
            self._last_seen.move_to_end(key)
            self._evict_if_needed()
            remaining = limit.requests - len(window)
            reset_epoch = (next(iter(window))[0] if window else now) + limit.window_seconds
            return RateLimitDecision(
                allowed=True,
                limit=limit.requests,
                remaining=remaining,
                reset_epoch=reset_epoch,
                retry_after_seconds=0,
            )
