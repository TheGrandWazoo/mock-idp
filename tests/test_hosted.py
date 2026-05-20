"""Tests for the hosted (Pro) slug router.

Sets MOCK_IDP_HOSTED=true before importing the app so the hosted router
is registered. Uses a mock OrgRegistry that bypasses Postgres — all
identity data comes from config.example.yaml via the YAML store.
"""

import base64
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

_ROOT = Path(__file__).parent.parent
_CONFIG = str(_ROOT / "config.example.yaml")

os.environ.setdefault("CONFIG_PATH", _CONFIG)
os.environ.setdefault("ISS_BASE", "http://localhost:8080")
# Note: MOCK_IDP_HOSTED is NOT set globally — the fixture builds its own app
# directly with the hosted router, avoiding env-var bleed into test_app.py.

# Import after env vars are set
from mock_idp.keys import _IssuerKeys  # noqa: E402
from mock_idp.org_registry import OrgContext  # noqa: E402
from mock_idp.store.yaml_store import YamlIdentityStore  # noqa: E402


# ── Fixtures ───────────────────────────────────────────────────────────────


class _InMemoryKeyResolver:
    """Key resolver backed by an in-memory _IssuerKeys — no Postgres needed."""

    def __init__(self, org_id: str) -> None:
        self._org_id = org_id
        self._cache: dict[str, _IssuerKeys] = {}

    def _get(self, issuer: str) -> _IssuerKeys:
        if issuer not in self._cache:
            self._cache[issuer] = _IssuerKeys(f"{self._org_id}/{issuer}")
        return self._cache[issuer]

    def signing_key_for_alg(self, issuer: str, alg: str):
        return self._get(issuer).signing_key_for_alg(alg)

    def alt_key_for_alg(self, issuer: str, alg: str):
        return self._get(issuer).alt_key_for_alg(alg)

    def jwks_keys(self, issuer: str):
        return self._get(issuer).jwks_keys()

    def signing_public_key_pem(self, issuer: str) -> bytes:
        return self._get(issuer).signing_public_key_pem()

    async def async_signing_key_for_alg(self, issuer: str, alg: str):
        return self.signing_key_for_alg(issuer, alg)

    async def async_alt_key_for_alg(self, issuer: str, alg: str):
        return self.alt_key_for_alg(issuer, alg)

    async def async_jwks_keys(self, issuer: str):
        return self.jwks_keys(issuer)

    async def async_signing_public_key_pem(self, issuer: str) -> bytes:
        return self.signing_public_key_pem(issuer)


def _make_org(slug: str, tier: str = "pro", api_key: str | None = "test-api-key") -> OrgContext:
    import hashlib
    store = YamlIdentityStore(Path(_CONFIG))  # loads synchronously in __init__
    key_resolver = _InMemoryKeyResolver(slug)
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest() if api_key else None
    return OrgContext(
        org_id=slug,
        slug=slug,
        tier=tier,
        api_key_hash=api_key_hash,
        store=store,
        key_resolver=key_resolver,
    )


_TEST_API_KEY = "test-api-key"
_TEST_ORG = "acme-corp"
_TEST_SANDBOX = "sandbox"

_acme_ctx = _make_org(_TEST_ORG, tier="pro", api_key=_TEST_API_KEY)
_sandbox_ctx = _make_org(_TEST_SANDBOX, tier="sandbox", api_key=None)

_mock_registry = {_TEST_ORG: _acme_ctx, _TEST_SANDBOX: _sandbox_ctx}


def _registry_get(slug: str):
    return _mock_registry.get(slug)


@pytest.fixture(scope="module")
def client():
    """Build a minimal FastAPI app with just the hosted router.

    We cannot reuse mock_idp.main.app because it may have been imported without
    MOCK_IDP_HOSTED=true (e.g. by test_app.py running earlier in the same
    process). Building a fresh app with the hosted router avoids the stale
    module-level _HOSTED flag.
    """
    import threading
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from mock_idp import org_registry as _reg_module
    from mock_idp.routers import hosted as _hosted_module

    real_registry = _reg_module.registry
    mock_reg = MagicMock()
    mock_reg.get = _registry_get
    mock_reg._lock = threading.Lock()
    mock_reg._orgs = _mock_registry
    mock_reg.startup = AsyncMock()
    mock_reg.shutdown = AsyncMock()
    _reg_module.registry = mock_reg

    @asynccontextmanager
    async def _lifespan(app):
        yield

    test_app = FastAPI(lifespan=_lifespan)
    test_app.include_router(_hosted_module.router)

    try:
        with TestClient(test_app) as c:
            yield c
    finally:
        _reg_module.registry = real_registry


# ── Discovery ──────────────────────────────────────────────────────────────


def test_hosted_discovery(client):
    r = client.get(
        f"/{_TEST_ORG}/default/.well-known/openid-configuration",
        headers={"Authorization": f"Bearer {_TEST_API_KEY}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert f"/{_TEST_ORG}/default" in data["issuer"]
    assert f"/{_TEST_ORG}/default/token" in data["token_endpoint"]
    assert f"/{_TEST_ORG}/default/jwks" in data["jwks_uri"]


# ── JWKS ───────────────────────────────────────────────────────────────────


def test_hosted_jwks_returns_keys(client):
    r = client.get(
        f"/{_TEST_ORG}/default/jwks",
        headers={"Authorization": f"Bearer {_TEST_API_KEY}"},
    )
    assert r.status_code == 200
    keys = r.json()["keys"]
    assert len(keys) >= 1
    assert any(k["kty"] == "RSA" for k in keys)


def test_hosted_jwks_stable_on_repeated_calls(client):
    """Same kids returned on two consecutive calls — keys are stable."""
    headers = {"Authorization": f"Bearer {_TEST_API_KEY}"}
    kids1 = [k["kid"] for k in client.get(f"/{_TEST_ORG}/default/jwks", headers=headers).json()["keys"]]
    kids2 = [k["kid"] for k in client.get(f"/{_TEST_ORG}/default/jwks", headers=headers).json()["keys"]]
    assert kids1 == kids2


# ── Token — password grant ─────────────────────────────────────────────────


def test_hosted_password_grant(client):
    r = client.post(
        f"/{_TEST_ORG}/default/token",
        data={
            "grant_type": "password",
            "username": "alice",
            "password": "alice-pw",
            "resource": "api://serviceB",
        },
        headers={"Authorization": f"Bearer {_TEST_API_KEY}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body
    payload = _decode_payload(body["access_token"])
    assert f"/{_TEST_ORG}/default" in payload["iss"]
    assert payload["sub"] is not None


def test_hosted_password_grant_wrong_password(client):
    r = client.post(
        f"/{_TEST_ORG}/default/token",
        data={"grant_type": "password", "username": "alice", "password": "wrong",
              "resource": "api://serviceB"},
        headers={"Authorization": f"Bearer {_TEST_API_KEY}"},
    )
    assert r.status_code == 401


# ── Token — client credentials ─────────────────────────────────────────────


def test_hosted_client_credentials_grant(client):
    r = client.post(
        f"/{_TEST_ORG}/default/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "service-a",
            "client_secret": "serviceA-secret",
            "resource": "api://serviceB",
        },
        headers={"Authorization": f"Bearer {_TEST_API_KEY}"},
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert f"/{_TEST_ORG}/default" in payload["iss"]


# ── API key gating ─────────────────────────────────────────────────────────


def test_hosted_missing_api_key_returns_401(client):
    r = client.post(
        f"/{_TEST_ORG}/default/token",
        data={"grant_type": "password", "username": "alice", "password": "alice-pw",
              "resource": "api://serviceB"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "invalid_api_key"


def test_hosted_wrong_api_key_returns_401(client):
    r = client.post(
        f"/{_TEST_ORG}/default/token",
        data={"grant_type": "password", "username": "alice", "password": "alice-pw",
              "resource": "api://serviceB"},
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert r.status_code == 401


# ── Org not found ──────────────────────────────────────────────────────────


def test_hosted_unknown_org_returns_404(client):
    r = client.get("/nonexistent-org/default/jwks")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "org_not_found"


# ── Sandbox org ────────────────────────────────────────────────────────────


def test_sandbox_no_api_key_required(client):
    r = client.post(
        f"/{_TEST_SANDBOX}/default/token",
        data={"grant_type": "password", "username": "alice", "password": "alice-pw",
              "resource": "api://serviceB"},
    )
    assert r.status_code == 200


def test_sandbox_iss_includes_sandbox_slug(client):
    r = client.post(
        f"/{_TEST_SANDBOX}/default/token",
        data={"grant_type": "password", "username": "alice", "password": "alice-pw",
              "resource": "api://serviceB"},
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert f"/{_TEST_SANDBOX}/default" in payload["iss"]


# ── Org isolation ──────────────────────────────────────────────────────────


def test_org_isolation_separate_jwks(client):
    """acme-corp and sandbox have different JWKS key sets."""
    acme_kids = {k["kid"] for k in client.get(
        f"/{_TEST_ORG}/default/jwks",
        headers={"Authorization": f"Bearer {_TEST_API_KEY}"},
    ).json()["keys"]}
    sandbox_kids = {k["kid"] for k in client.get(f"/{_TEST_SANDBOX}/default/jwks").json()["keys"]}
    assert acme_kids.isdisjoint(sandbox_kids), "orgs must not share key IDs"


# ── Token signature verification ───────────────────────────────────────────


def test_hosted_token_validates_against_jwks(client):
    """Issue a token then verify its signature using the org's JWKS."""
    from joserfc import jwt
    from joserfc.jwk import KeySet

    token_r = client.post(
        f"/{_TEST_ORG}/default/token",
        data={"grant_type": "password", "username": "alice", "password": "alice-pw",
              "resource": "api://serviceB"},
        headers={"Authorization": f"Bearer {_TEST_API_KEY}"},
    )
    token = token_r.json()["access_token"]

    jwks_r = client.get(
        f"/{_TEST_ORG}/default/jwks",
        headers={"Authorization": f"Bearer {_TEST_API_KEY}"},
    )
    key_set = KeySet.import_key_set(jwks_r.json())
    decoded = jwt.decode(token, key_set)
    assert decoded.claims["sub"] is not None


# ── Helpers ────────────────────────────────────────────────────────────────


def _decode_payload(token: str) -> dict:
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))
