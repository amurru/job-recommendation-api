"""SH-001: identity model, API-key store, and the identity dependency."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from tests.conftest import make_settings

from job_recommendation_api.api.deps import get_identity
from job_recommendation_api.auth import ApiKeyStore, Identity, parse_keys_file
from job_recommendation_api.config import Settings


def _store(*keys: str) -> ApiKeyStore:
    return ApiKeyStore(list(keys))


class TestApiKeyStore:
    def test_valid_key_resolves_to_key_identity(self) -> None:
        store = _store("alpha-key", "beta-key")
        identity = store.verify("beta-key")
        assert identity is not None
        assert identity.kind == "key"
        assert identity.key_id == hashlib.sha256(b"beta-key").hexdigest()[:12]

    def test_unknown_key_returns_none(self) -> None:
        store = _store("alpha-key")
        assert store.verify("nope") is None

    def test_empty_store_returns_none(self) -> None:
        assert _store().verify("anything") is None

    def test_digest_store_never_contains_plaintext(self) -> None:
        """Guardrail: the store keeps SHA-256 digests only."""
        secret = "super-secret-plaintext-key"
        store = _store(secret)
        # repr and public surface must not leak the plaintext.
        assert secret not in repr(store)
        for digest, _key_id in store._digests.items():  # noqa: SLF001 - deliberate probe
            assert secret.encode() not in digest
            assert len(digest) == 32  # raw SHA-256
        # The only key-derived value is the digest prefix.
        identity = store.verify(secret)
        assert identity is not None
        assert identity.key_id == hashlib.sha256(secret.encode()).hexdigest()[:12]

    def test_duplicate_keys_deduplicated(self) -> None:
        store = _store("dup", "dup")
        assert len(store) == 1

    def test_whitespace_keys_ignored(self) -> None:
        store = _store("  ", "", "real-key")
        assert len(store) == 1
        assert store.verify("real-key") is not None


class TestKeysFile:
    def test_parse_keys_file_comments_and_blanks(self, tmp_path: Path) -> None:
        key_file = tmp_path / "keys.txt"
        key_file.write_text(
            "# production keys\nkey-one\n\nkey-two  # inline comment\n   \nkey-three\n",
            encoding="utf-8",
        )
        assert parse_keys_file(key_file) == ["key-one", "key-two", "key-three"]

    def test_from_settings_combines_env_and_file(self, tmp_path: Path) -> None:
        key_file = tmp_path / "keys.txt"
        key_file.write_text("file-key\n", encoding="utf-8")
        settings = make_settings(api_keys="env-key", api_keys_file=key_file)
        store = ApiKeyStore.from_settings(settings)
        assert store.verify("env-key") is not None
        assert store.verify("file-key") is not None
        assert store.verify("other") is None


class TestIdentityDependency:
    def _app(self, settings: Settings) -> TestClient:
        from job_recommendation_api.auth import ApiKeyStore
        from job_recommendation_api.main import create_app

        app: FastAPI = create_app(settings)
        app.state.api_key_store = ApiKeyStore.from_settings(settings)

        @app.get("/probe")
        def probe(identity: Identity = Depends(get_identity)) -> dict[str, str | None]:
            return {"kind": identity.kind, "key_id": identity.key_id}

        return TestClient(app)

    def test_no_header_anonymous_allowed(self) -> None:
        client = self._app(make_settings(log_level="ERROR"))
        resp = client.get("/probe")
        assert resp.status_code == 200
        assert resp.json()["kind"] == "anonymous"

    def test_no_header_anonymous_disabled_401(self) -> None:
        client = self._app(make_settings(anonymous_enabled=False, log_level="ERROR"))
        resp = client.get("/probe")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"
        assert resp.headers["WWW-Authenticate"] == "Bearer"

    def test_auth_required_blocks_anonymous(self) -> None:
        client = self._app(make_settings(auth_required=True, log_level="ERROR"))
        assert client.get("/probe").status_code == 401

    def test_valid_key_resolves(self) -> None:
        client = self._app(make_settings(api_keys="k1", log_level="ERROR"))
        resp = client.get("/probe", headers={"Authorization": "Bearer k1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "key"
        assert body["key_id"] == hashlib.sha256(b"k1").hexdigest()[:12]

    def test_unknown_key_401(self) -> None:
        client = self._app(make_settings(api_keys="k1", log_level="ERROR"))
        resp = client.get("/probe", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401
        assert resp.headers["WWW-Authenticate"] == "Bearer"

    def test_non_bearer_scheme_401(self) -> None:
        client = self._app(make_settings(api_keys="k1", log_level="ERROR"))
        for header in ("Basic k1", "Bearer", "Token k1"):
            resp = client.get("/probe", headers={"Authorization": header})
            assert resp.status_code == 401, header

    def test_case_insensitive_bearer_scheme(self) -> None:
        client = self._app(make_settings(api_keys="k1", log_level="ERROR"))
        resp = client.get("/probe", headers={"Authorization": "bearer k1"})
        assert resp.status_code == 200
