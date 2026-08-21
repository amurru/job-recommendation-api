"""SH-016: security integration matrix.

Covers the full hardening surface end to end: auth (SH-002), rate limiting
(SH-005), conversion concurrency (SH-006), docs gating (SH-010), validation
sanitization (SH-011), CORS (SH-012), and diagnostics gating + security
headers (SH-013)."""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from tests.api.test_integration import MINIMAL_PDF, _FakeConverter, _FakeLLM, _FakeProfiler
from tests.conftest import make_settings

from job_recommendation_api.api.deps import get_recommendation_service
from job_recommendation_api.main import create_app
from job_recommendation_api.services.extraction_cache import InMemoryExtractionCache
from job_recommendation_api.services.recommendation import RecommendationService

KEY = "test-key-123"
AUTH = {"Authorization": f"Bearer {KEY}"}


def _security_app(**overrides: Any) -> TestClient:
    """App with a fake recommendation service (no LLM/conversion cost)."""
    base: dict[str, Any] = {
        "openrouter_api_key": SecretStr("sk-test-key"),
        "log_level": "ERROR",
    }
    base.update(overrides)
    app = create_app(make_settings(**base))
    profiler = _FakeProfiler()

    def _service() -> RecommendationService:
        return RecommendationService(
            _FakeConverter(),
            _FakeLLM(),
            model="test/model",
            profiler=profiler,
            extraction_cache=InMemoryExtractionCache(max_entries=16, ttl_seconds=60),
        )

    app.dependency_overrides[get_recommendation_service] = _service
    return TestClient(app)


def _post(client: TestClient, **kwargs: Any) -> Any:
    return client.post(
        "/api/v1/recommendations",
        files={"file": ("resume.pdf", MINIMAL_PDF, "application/pdf")},
        **kwargs,
    )


class TestAuthentication:
    def test_anonymous_post_still_succeeds(self) -> None:
        """Constraint: an anonymous POST with no Authorization header still
        succeeds by default (zero-friction try-it promise)."""
        client = _security_app()
        resp = _post(client)
        assert resp.status_code == 200, resp.text
        assert "analysis" in resp.json()

    def test_bad_key_401_uniform_envelope(self) -> None:
        client = _security_app(api_keys=KEY)
        resp = _post(client, headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401
        body = resp.json()
        assert set(body.keys()) == {"error"}
        assert body["error"]["code"] == "unauthorized"
        assert resp.headers["WWW-Authenticate"] == "Bearer"

    def test_bad_scheme_401(self) -> None:
        client = _security_app(api_keys=KEY)
        resp = _post(client, headers={"Authorization": "Basic " + KEY})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"

    def test_valid_key_accepted(self) -> None:
        client = _security_app(api_keys=KEY)
        resp = _post(client, headers=AUTH)
        assert resp.status_code == 200, resp.text

    def test_anonymous_disabled_401(self) -> None:
        client = _security_app(anonymous_enabled=False, api_keys=KEY)
        resp = _post(client)
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"

    def test_auth_required_blocks_anonymous(self) -> None:
        client = _security_app(auth_required=True, api_keys=KEY)
        assert _post(client).status_code == 401
        assert _post(client, headers=AUTH).status_code == 200

    def test_health_routes_stay_open(self) -> None:
        """Health probes are never authenticated, even key-only."""
        client = _security_app(anonymous_enabled=False, api_keys=KEY)
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200

    def test_401_never_invokes_pipeline(self) -> None:
        """The 401 fires before any parsing/conversion/LLM work."""
        client = _security_app(api_keys=KEY)
        resp = _post(client, headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401
        # No X-Cache header: the pipeline never produced a result.
        assert "X-Cache" not in resp.headers

    def test_401_consumes_no_rate_limit_budget(self) -> None:
        """SH-002: invalid credentials do not consume budget - the 401 is
        raised before the limiter check."""
        client = _security_app(api_keys=KEY, rate_limit_anon_requests=2)
        for _ in range(5):
            resp = _post(client, headers={"Authorization": "Bearer wrong"})
            assert resp.status_code == 401
        # The anonymous budget is untouched: anonymous requests still work.
        assert _post(client).status_code == 200


class TestRateLimiting:
    def test_anon_burst_hits_429_with_headers(self) -> None:
        client = _security_app(rate_limit_anon_requests=2)
        assert _post(client).status_code == 200
        assert _post(client).status_code == 200
        denied = _post(client)
        assert denied.status_code == 429
        body = denied.json()
        assert body["error"]["code"] == "rate_limited"
        assert denied.headers["Retry-After"].isdigit()
        assert denied.headers["X-RateLimit-Limit"] == "2"
        assert denied.headers["X-RateLimit-Remaining"] == "0"
        int(denied.headers["X-RateLimit-Reset"])  # epoch seconds

    def test_recovery_after_window(self) -> None:
        client = _security_app(rate_limit_anon_requests=1, rate_limit_anon_window_seconds=1.0)
        assert _post(client).status_code == 200
        assert _post(client).status_code == 429
        time.sleep(1.1)
        assert _post(client).status_code == 200

    def test_success_responses_carry_rate_limit_headers(self) -> None:
        client = _security_app(rate_limit_anon_requests=5)
        resp = _post(client)
        assert resp.headers["X-RateLimit-Limit"] == "5"
        assert resp.headers["X-RateLimit-Remaining"] == "4"

    def test_authenticated_tier_has_own_budget(self) -> None:
        client = _security_app(api_keys=KEY, rate_limit_anon_requests=1)
        # The anonymous tier is exhausted...
        assert _post(client).status_code == 200
        assert _post(client).status_code == 429
        # ...but the keyed tier has its own window.
        keyed = _post(client, headers=AUTH)
        assert keyed.status_code == 200
        assert keyed.headers["X-RateLimit-Limit"] == "60"

    def test_rate_limit_disabled_allows_everything(self) -> None:
        client = _security_app(rate_limit_enabled=False, rate_limit_anon_requests=1)
        for _ in range(3):
            assert _post(client).status_code == 200
        resp = _post(client)
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" not in resp.headers


class TestConversionConcurrency:
    def test_second_request_waits_while_saturated(self) -> None:
        """SH-006: with concurrency 1 and a blocked converter, the second
        request waits; after the first completes, the second proceeds."""
        import threading

        release = threading.Event()
        started = threading.Event()

        class _BlockingConverter:
            def convert(self, document_bytes: bytes, *, name: str) -> str:
                started.set()
                release.wait(timeout=5)
                return "# Jane\nPython developer\njane@example.com"

        app = create_app(
            make_settings(
                openrouter_api_key=SecretStr("sk-test-key"),
                log_level="ERROR",
                convert_concurrency=1,
            )
        )
        profiler = _FakeProfiler()

        def _service() -> RecommendationService:
            return RecommendationService(
                _BlockingConverter(),
                _FakeLLM(),
                model="test/model",
                profiler=profiler,
                extraction_cache=InMemoryExtractionCache(max_entries=16, ttl_seconds=60),
            )

        app.dependency_overrides[get_recommendation_service] = _service
        client = TestClient(app)

        import queue

        q: queue.Queue[int] = queue.Queue()

        def first() -> None:
            q.put(_post(client).status_code)

        t1 = threading.Thread(target=first)
        t1.start()
        assert started.wait(timeout=5), "first conversion never started"
        t2 = threading.Thread(target=lambda: q.put(_post(client).status_code))
        t2.start()
        time.sleep(0.3)  # give request 2 time to reach the limiter wait
        release.set()
        t1.join(timeout=10)
        t2.join(timeout=10)
        statuses = [q.get(timeout=5), q.get(timeout=5)]
        assert sorted(statuses) == [200, 200]

    def test_limiter_not_held_during_llm_calls(self) -> None:
        """SH-006 guardrail: the capacity limiter scopes only the sync
        conversion. A cached (conversion-free) request must proceed while
        the limiter would have been held by a slow conversion."""
        import threading

        release = threading.Event()
        started = threading.Event()

        class _BlockingConverter:
            def convert(self, document_bytes: bytes, *, name: str) -> str:
                started.set()
                release.wait(timeout=5)
                return "# Jane\nPython developer\njane@example.com"

        app = create_app(
            make_settings(
                openrouter_api_key=SecretStr("sk-test-key"),
                log_level="ERROR",
                convert_concurrency=1,
                api_keys=KEY,
            )
        )
        profiler = _FakeProfiler()
        cache = InMemoryExtractionCache(max_entries=16, ttl_seconds=60)

        def _service() -> RecommendationService:
            return RecommendationService(
                _BlockingConverter(),
                _FakeLLM(),
                model="test/model",
                profiler=profiler,
                extraction_cache=cache,
            )

        app.dependency_overrides[get_recommendation_service] = _service
        client = TestClient(app)

        done = threading.Event()

        def slow_request() -> None:
            _post(client, headers=AUTH)
            done.set()

        t1 = threading.Thread(target=slow_request)
        t1.start()
        assert started.wait(timeout=5)
        # Same document bytes -> extraction cache HIT -> no conversion -> no
        # limiter acquisition, even though conversion #1 is still running.
        cached = _post(client, headers=AUTH)
        assert cached.status_code == 200, cached.text
        release.set()
        assert done.wait(timeout=10)
        t1.join(timeout=10)


class TestDocsGating:
    def test_production_hides_docs(self) -> None:
        client = _security_app()
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/redoc").status_code == 404

    def test_development_serves_docs(self) -> None:
        client = _security_app(environment="development")
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200

    def test_explicit_override_serves_docs_in_production(self) -> None:
        client = _security_app(docs_enabled=True)
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200

    def test_explicit_override_hides_docs_in_development(self) -> None:
        client = _security_app(environment="development", docs_enabled=False)
        assert client.get("/docs").status_code == 404


class TestValidationSanitization:
    def test_malformed_request_returns_generic_422(self) -> None:
        """SH-011: no pydantic detail, no echoed input fragments."""
        client = _security_app()
        resp = client.post(
            "/api/v1/recommendations?include_meta=NOT_A_BOOL",
            files={"file": ("resume.pdf", MINIMAL_PDF, "application/pdf")},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "validation_error"
        assert set(body["error"].keys()) == {"code", "message"}
        assert "NOT_A_BOOL" not in resp.text
        assert "detail" not in body["error"]

    def test_missing_file_field_generic_422(self) -> None:
        client = _security_app()
        resp = client.post("/api/v1/recommendations")
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"


class TestCors:
    def test_no_middleware_when_unset(self) -> None:
        client = _security_app()
        resp = client.options(
            "/api/v1/recommendations",
            headers={"Origin": "https://example.com", "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}

    def test_allowed_origin_gets_cors_headers(self) -> None:
        client = _security_app(cors_origins="https://good.example.com")
        resp = client.options(
            "/api/v1/recommendations",
            headers={"Origin": "https://good.example.com", "Access-Control-Request-Method": "POST"},
        )
        assert resp.headers["access-control-allow-origin"] == "https://good.example.com"
        # Starlette omits the header entirely when allow_credentials=False:
        # credentials are never allowed (SH-012 guardrail).
        assert "access-control-allow-credentials" not in resp.headers

    def test_disallowed_origin_gets_no_cors_headers(self) -> None:
        client = _security_app(cors_origins="https://good.example.com")
        resp = client.options(
            "/api/v1/recommendations",
            headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}

    def test_wildcard_origin_rejected_at_startup(self) -> None:
        with pytest.raises(Exception, match=r"\*"):
            _security_app(cors_origins="*")

    def test_wildcard_in_list_rejected_at_startup(self) -> None:
        with pytest.raises(Exception, match=r"\*"):
            _security_app(cors_origins="https://good.example.com,*")


class TestDiagnosticsGating:
    def test_anonymous_include_meta_omitted(self) -> None:
        client = _security_app()
        resp = _post(client, params={"include_meta": "true"})
        assert resp.status_code == 200
        assert "meta" not in resp.json()

    def test_keyed_include_meta_included(self) -> None:
        client = _security_app(api_keys=KEY)
        resp = _post(client, headers=AUTH, params={"include_meta": "true"})
        assert resp.status_code == 200
        assert resp.json()["meta"]["model"] == "test/model"

    def test_development_mode_includes_meta_for_anonymous(self) -> None:
        client = _security_app(environment="development")
        resp = _post(client)
        assert resp.status_code == 200
        assert "meta" in resp.json()

    def test_production_readyz_hides_model_from_anonymous(self) -> None:
        client = _security_app()
        body = client.get("/readyz").json()
        assert body == {"status": "ready"}

    def test_production_readyz_shows_model_to_keyed(self) -> None:
        client = _security_app(api_keys=KEY)
        body = client.get("/readyz", headers=AUTH).json()
        assert body["status"] == "ready"
        assert body["model"] == "openai/gpt-4o-mini"

    def test_development_readyz_shows_model(self) -> None:
        client = _security_app(environment="development")
        body = client.get("/readyz").json()
        assert body["model"] == "openai/gpt-4o-mini"

    def test_invalid_key_on_readyz_treated_as_anonymous(self) -> None:
        """readyz never 401s; a bad key just gets the minimal body."""
        client = _security_app(api_keys=KEY)
        resp = client.get("/readyz", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ready"}


class TestSecurityHeadersEverywhere:
    @pytest.mark.parametrize(
        ("method", "path", "kwargs"),
        [
            ("get", "/healthz", {}),
            ("get", "/readyz", {}),
            ("get", "/docs", {}),
            ("post", "/api/v1/recommendations", {}),
        ],
    )
    def test_headers_on_all_responses(self, method: str, path: str, kwargs: Any) -> None:
        client = _security_app()
        if method == "post":
            resp = _post(client, **kwargs)
        else:
            resp = getattr(client, method)(path, **kwargs)
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["Referrer-Policy"] == "no-referrer"
        assert resp.headers["X-Frame-Options"] == "DENY"
