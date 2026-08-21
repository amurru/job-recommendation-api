"""SH-004: sliding-window rate limiter - windows, isolation, LRU bound,
thread safety, disabled mode. Uses an injected clock: no timing flakiness."""

from __future__ import annotations

import threading

from job_recommendation_api.auth import Identity
from job_recommendation_api.ratelimit import SlidingWindowRateLimiter, WindowLimit


class FakeClock:
    """Deterministic monotonic clock advanced explicitly by tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _anon(ip: str = "1.2.3.4") -> Identity:
    return Identity(kind="anonymous", ip=ip)


def _keyed(key_id: str = "abc123") -> Identity:
    return Identity(kind="key", key_id=key_id)


def _limiter(
    clock: FakeClock, *, anon_requests: int = 2, window: float = 3600.0
) -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(
        limits={
            "anonymous": WindowLimit(requests=anon_requests, window_seconds=window),
            "key": WindowLimit(requests=10, window_seconds=60.0),
        },
        clock=clock,
    )


class TestWindowBehavior:
    def test_requests_within_window_allowed_then_blocked(self) -> None:
        clock = FakeClock()
        limiter = _limiter(clock)
        assert limiter.check(_anon()).allowed
        assert limiter.check(_anon()).allowed
        denied = limiter.check(_anon())
        assert not denied.allowed
        assert denied.retry_after_seconds > 0

    def test_allowed_again_after_window_expiry(self) -> None:
        clock = FakeClock()
        limiter = _limiter(clock)
        limiter.check(_anon())
        limiter.check(_anon())
        assert not limiter.check(_anon()).allowed
        clock.advance(3600.0)
        decision = limiter.check(_anon())
        assert decision.allowed

    def test_retry_after_decrements_to_allow(self) -> None:
        clock = FakeClock()
        limiter = _limiter(clock, window=100.0)
        limiter.check(_anon())
        limiter.check(_anon())
        denied = limiter.check(_anon())
        assert not denied.allowed
        clock.advance(denied.retry_after_seconds)
        assert limiter.check(_anon()).allowed

    def test_decision_header_fields(self) -> None:
        clock = FakeClock()
        limiter = _limiter(clock)
        decision = limiter.check(_anon())
        assert decision.limit == 2
        assert decision.remaining == 1
        assert decision.reset_epoch == clock.now + 3600.0
        assert decision.retry_after_seconds == 0

    def test_reset_epoch_is_oldest_request_plus_window(self) -> None:
        clock = FakeClock()
        limiter = _limiter(clock)
        first = limiter.check(_anon())
        clock.advance(10.0)
        limiter.check(_anon())
        clock.advance(10.0)
        denied = limiter.check(_anon())
        assert not denied.allowed
        assert denied.reset_epoch == first.reset_epoch


class TestPerIdentityIsolation:
    def test_anonymous_ips_isolated(self) -> None:
        clock = FakeClock()
        limiter = _limiter(clock)
        limiter.check(_anon("1.1.1.1"))
        limiter.check(_anon("1.1.1.1"))
        assert not limiter.check(_anon("1.1.1.1")).allowed
        # A different IP has its own window.
        assert limiter.check(_anon("2.2.2.2")).allowed

    def test_keyed_identities_isolated_from_anonymous(self) -> None:
        clock = FakeClock()
        limiter = _limiter(clock)
        limiter.check(_anon())
        first = limiter.check(_keyed())
        assert first.allowed
        assert first.remaining == 9  # anonymous activity left the key budget intact

    def test_keyed_identities_isolated_per_key(self) -> None:
        clock = FakeClock()
        limiter = _limiter(clock)
        for _ in range(10):
            limiter.check(_keyed("k1"))
        assert not limiter.check(_keyed("k1")).allowed
        assert limiter.check(_keyed("k2")).allowed


class TestBoundedState:
    def test_lru_eviction_caps_tracked_identities(self) -> None:
        clock = FakeClock()
        limiter = SlidingWindowRateLimiter(
            limits={"anonymous": WindowLimit(requests=1, window_seconds=3600.0)},
            max_tracked_identities=3,
            clock=clock,
        )
        for i in range(5):
            limiter.check(_anon(f"10.0.0.{i}"))
        assert len(limiter._windows) <= 3  # noqa: SLF001 - deliberate probe

    def test_evicted_identity_restarts_window(self) -> None:
        """Bounded-harm semantics: eviction lets a flooded identity restart
        its window (documented SH-ADR-003 trade-off)."""
        clock = FakeClock()
        limiter = SlidingWindowRateLimiter(
            limits={"anonymous": WindowLimit(requests=1, window_seconds=3600.0)},
            max_tracked_identities=2,
            clock=clock,
        )
        limiter.check(_anon("a"))
        limiter.check(_anon("b"))
        assert not limiter.check(_anon("a")).allowed  # a is now most-recent
        # Two new identities evict the two LRU entries (b, then a).
        limiter.check(_anon("c"))
        limiter.check(_anon("d"))
        # a's window was evicted: its budget restarts.
        assert limiter.check(_anon("a")).allowed

    def test_stale_entries_pruned_on_access(self) -> None:
        """Lazy pruning (per SH-ADR-003): an identity's expired entries are
        dropped when that identity is checked again, and a fully expired
        window releases its tracked-identity slot."""
        clock = FakeClock()
        limiter = _limiter(clock)
        limiter.check(_anon("10.0.0.0"))
        assert len(limiter._windows["anon:10.0.0.0"]) == 1  # noqa: SLF001
        clock.advance(3600.0)
        limiter.check(_anon("10.0.0.0"))
        # The stale entry is gone; only the fresh request remains.
        assert len(limiter._windows["anon:10.0.0.0"]) == 1  # noqa: SLF001
        assert list(limiter._windows["anon:10.0.0.0"])[0][0] == clock.now  # noqa: SLF001


class TestThreadSafety:
    def test_concurrent_checks_never_exceed_budget(self) -> None:
        clock = FakeClock()
        limiter = SlidingWindowRateLimiter(
            limits={"anonymous": WindowLimit(requests=50, window_seconds=3600.0)},
            clock=clock,
        )
        allowed_count = 0
        lock = threading.Lock()

        def hammer() -> None:
            nonlocal allowed_count
            for _ in range(20):
                if limiter.check(_anon("shared-ip")).allowed:
                    with lock:
                        allowed_count += 1

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert allowed_count == 50


class TestDisabledMode:
    def test_unconfigured_tier_is_unlimited(self) -> None:
        clock = FakeClock()
        limiter = SlidingWindowRateLimiter(
            limits={"anonymous": WindowLimit(requests=1, window_seconds=3600.0)},
            clock=clock,
        )
        # "key" tier not configured -> unlimited.
        for _ in range(100):
            assert limiter.check(_keyed()).allowed
