"""Tests for the mock OIDC server."""

import base64
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault(
    "CONFIG_PATH",
    str(((__import__("pathlib").Path(__file__).parent.parent) / "config.example.yaml")),
)
os.environ.setdefault("ISS_BASE", "http://localhost:8080")

from mock_idp.config import USERS  # noqa: E402
from mock_idp.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ── Health ─────────────────────────────────────────────────────────────────


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ── Discovery / JWKS ───────────────────────────────────────────────────────


def test_discovery(client):
    r = client.get("/default/.well-known/openid-configuration")
    assert r.status_code == 200
    data = r.json()
    assert "token_endpoint" in data
    assert data["issuer"].endswith("/default")


def test_jwks(client):
    r = client.get("/default/jwks")
    assert r.status_code == 200
    keys = r.json()["keys"]
    assert len(keys) == 1
    assert keys[0]["kty"] == "RSA"


# ── Password grant ─────────────────────────────────────────────────────────


def test_password_grant_happy_path(client):
    r = client.post(
        "/default/token",
        data={
            "grant_type": "password",
            "username": "alice",
            "password": "alice-pw",
            "resource": "api://serviceB",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert "access_token" in data
    payload = _decode_payload(data["access_token"])
    assert payload["aud"] == "api://serviceB"
    assert payload["preferred_username"] == "alice@example.com"
    assert payload["ver"] == "2.0"
    assert "operator" in payload["roles"]


def test_password_grant_wrong_password(client):
    r = client.post(
        "/default/token",
        data={
            "grant_type": "password",
            "username": "alice",
            "password": "wrong",
            "resource": "api://serviceB",
        },
    )
    assert r.status_code == 401


def test_password_grant_unknown_user(client):
    r = client.post(
        "/default/token",
        data={
            "grant_type": "password",
            "username": "nobody",
            "password": "pw",
            "resource": "api://serviceB",
        },
    )
    assert r.status_code == 401


def test_password_grant_v1_shape_header(client):
    r = client.post(
        "/default/token",
        headers={"X-Token-Shape": "v1"},
        data={
            "grant_type": "password",
            "username": "alice",
            "password": "alice-pw",
            "resource": "api://serviceB",
        },
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert payload["ver"] == "1.0"
    assert "upn" in payload
    assert "preferred_username" not in payload


def test_password_grant_with_client_id_adds_azp(client):
    r = client.post(
        "/default/token",
        data={
            "grant_type": "password",
            "username": "alice",
            "password": "alice-pw",
            "resource": "api://serviceB",
            "client_id": "service-a",
        },
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert "azp" in payload


# ── Grants model ───────────────────────────────────────────────────────────


def test_grants_resolve_correct_roles_for_audience(client):
    """Alice gets operator+responder on serviceB but only reader on serviceC."""
    r_b = client.post(
        "/default/token",
        data={"grant_type": "password", "username": "alice", "password": "alice-pw",
              "resource": "api://serviceB"},
    )
    r_c = client.post(
        "/default/token",
        data={"grant_type": "password", "username": "alice", "password": "alice-pw",
              "resource": "api://serviceC"},
    )
    roles_b = _decode_payload(r_b.json()["access_token"])["roles"]
    roles_c = _decode_payload(r_c.json()["access_token"])["roles"]
    assert sorted(roles_b) == ["operator", "responder"]
    assert roles_c == ["reader"]


def test_grants_sp_resolves_by_name_not_uuid(client):
    """service-a grant is resolved even when authenticating with UUID client_id."""
    r = client.post(
        "/default/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "01010101-1010-1010-1010-aaaaaaaaaaaa",
            "client_secret": "serviceA-secret",
            "resource": "api://serviceB",
        },
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert payload["roles"] == ["m2m"]


def test_grants_no_grant_returns_empty_roles_in_lax(client):
    """bob has no grant on serviceC — lax mode returns empty roles, not 400."""
    r = client.post(
        "/default/token",
        data={"grant_type": "password", "username": "bob", "password": "bob-pw",
              "resource": "api://serviceC"},
    )
    assert r.status_code == 200
    assert _decode_payload(r.json()["access_token"])["roles"] == []


# ── Client credentials ─────────────────────────────────────────────────────


def test_client_credentials_happy_path(client):
    r = client.post(
        "/default/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "service-a",
            "client_secret": "serviceA-secret",
            "resource": "api://serviceB",
        },
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert payload["appid"] == "01010101-1010-1010-1010-aaaaaaaaaaaa"
    assert payload["ver"] == "1.0"


def test_client_credentials_by_uuid(client):
    r = client.post(
        "/default/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "01010101-1010-1010-1010-aaaaaaaaaaaa",
            "client_secret": "serviceA-secret",
            "resource": "api://serviceB",
        },
    )
    assert r.status_code == 200


def test_client_credentials_wrong_secret(client):
    r = client.post(
        "/default/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "service-a",
            "client_secret": "wrong",
            "resource": "api://serviceB",
        },
    )
    assert r.status_code == 401


# ── Audience resolution ────────────────────────────────────────────────────


def test_aud_from_resource(client):
    r = client.post(
        "/default/token",
        data={
            "grant_type": "password",
            "username": "alice",
            "password": "alice-pw",
            "resource": "api://serviceB",
        },
    )
    assert _decode_payload(r.json()["access_token"])["aud"] == "api://serviceB"


def test_aud_from_scope_default_suffix(client):
    r = client.post(
        "/default/token",
        data={
            "grant_type": "password",
            "username": "alice",
            "password": "alice-pw",
            "scope": "api://serviceB/.default",
        },
    )
    assert _decode_payload(r.json()["access_token"])["aud"] == "api://serviceB"


def test_aud_resource_wins_over_scope(client):
    r = client.post(
        "/default/token",
        data={
            "grant_type": "password",
            "username": "alice",
            "password": "alice-pw",
            "resource": "api://serviceB",
            "scope": "api://serviceC/.default",
        },
    )
    assert _decode_payload(r.json()["access_token"])["aud"] == "api://serviceB"


def test_aud_default_when_neither(client):
    r = client.post(
        "/default/token",
        data={
            "grant_type": "password",
            "username": "alice",
            "password": "alice-pw",
        },
    )
    assert _decode_payload(r.json()["access_token"])["aud"] == "api://default"


# ── Strict mode ────────────────────────────────────────────────────────────


def test_strict_mode_rejects_unlisted_audience(client):
    import mock_idp.config as m

    original = m.MODE
    m.MODE = "strict"
    try:
        r = client.post(
            "/default/token",
            data={
                "grant_type": "password",
                "username": "alice",
                "password": "alice-pw",
                "resource": "api://serviceZ",
            },
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_target"
    finally:
        m.MODE = original


def test_strict_mode_allows_listed_audience(client):
    import mock_idp.config as m

    original = m.MODE
    m.MODE = "strict"
    try:
        r = client.post(
            "/default/token",
            data={
                "grant_type": "password",
                "username": "alice",
                "password": "alice-pw",
                "resource": "api://serviceB",
            },
        )
        assert r.status_code == 200
    finally:
        m.MODE = original


def test_strict_mode_rejects_identity_without_grant(client):
    """bob has no grant on serviceC — strict mode rejects it."""
    import mock_idp.config as m

    original = m.MODE
    m.MODE = "strict"
    try:
        r = client.post(
            "/default/token",
            data={"grant_type": "password", "username": "bob", "password": "bob-pw",
                  "resource": "api://serviceC"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_target"
    finally:
        m.MODE = original


# ── Admin override ─────────────────────────────────────────────────────────


def test_admin_override_injects_custom_claims(client):
    r = client.post(
        "/default/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "00000000-0000-0000-0000-000000000000",
            "client_secret": "admin-secret",
            "resource": "api://anywhere",
            "roles": "superuser,admin",
            "custom_claim": "custom-value",
        },
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert payload["roles"] == ["superuser", "admin"]
    assert payload["custom_claim"] == "custom-value"


def test_admin_iss_override_blocked_without_flag(client):
    """iss cannot be overridden unless override_iss_too is also set."""
    r = client.post(
        "/default/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "00000000-0000-0000-0000-000000000000",
            "client_secret": "admin-secret",
            "resource": "api://anywhere",
            "iss": "https://evil.example.com",
        },
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert payload["iss"] != "https://evil.example.com"


def test_admin_iss_override_allowed_with_flag(client):
    """iss can be overridden when override_iss_too is True."""
    import mock_idp.config as m

    admin_sp = m.SERVICE_PRINCIPALS["00000000-0000-0000-0000-000000000000"]
    original = admin_sp.override_iss_too
    admin_sp.override_iss_too = True
    try:
        r = client.post(
            "/default/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "00000000-0000-0000-0000-000000000000",
                "client_secret": "admin-secret",
                "resource": "api://anywhere",
                "iss": "https://evil.example.com",
            },
        )
        assert r.status_code == 200
        payload = _decode_payload(r.json()["access_token"])
        assert payload["iss"] == "https://evil.example.com"
    finally:
        admin_sp.override_iss_too = original


# ── Test override headers ──────────────────────────────────────────────────


def test_x_test_expired(client):
    r = client.post(
        "/default/token",
        headers={"X-Test-Expired": "1"},
        data={
            "grant_type": "password",
            "username": "alice",
            "password": "alice-pw",
            "resource": "api://serviceB",
        },
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert payload["exp"] < int(time.time())


def test_x_omit_claims(client):
    r = client.post(
        "/default/token",
        headers={"X-Omit-Claims": "oid,tid"},
        data={
            "grant_type": "password",
            "username": "alice",
            "password": "alice-pw",
            "resource": "api://serviceB",
        },
    )
    payload = _decode_payload(r.json()["access_token"])
    assert "oid" not in payload
    assert "tid" not in payload


# ── Negative endpoints ─────────────────────────────────────────────────────


def test_wrong_sig_endpoint(client):
    r = client.post(
        "/default/token/wrong-sig",
        data={
            "grant_type": "client_credentials",
            "client_id": "service-a",
            "client_secret": "serviceA-secret",
            "resource": "api://serviceB",
        },
    )
    assert r.status_code == 200
    import base64
    import json as _json

    header_part = r.json()["access_token"].split(".")[0]
    header_part += "=" * (-len(header_part) % 4)
    header = _json.loads(base64.urlsafe_b64decode(header_part))
    jwks_r = client.get("/default/jwks")
    published_kid = jwks_r.json()["keys"][0]["kid"]
    assert header["kid"] != published_kid


def test_malformed_endpoint(client):
    r = client.get("/default/token/malformed")
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert len(token.split(".")) == 3


# ── Multi-issuer ───────────────────────────────────────────────────────────


def test_multi_issuer_distinct_iss(client):
    for slug in ("tenant-a", "tenant-b"):
        r = client.post(
            f"/{slug}/token",
            data={
                "grant_type": "password",
                "username": "alice",
                "password": "alice-pw",
                "resource": "api://serviceB",
            },
        )
        payload = _decode_payload(r.json()["access_token"])
        assert payload["iss"].endswith(f"/{slug}")


# ── Debug endpoints ────────────────────────────────────────────────────────


def test_debug_identities_redacts_secrets(client):
    r = client.get("/debug/identities")
    assert r.status_code == 200
    data = r.json()
    for rec in data["users"].values():
        assert rec["password"] == "***"
    for rec in data["service_principals"].values():
        assert rec["secret"] == "***"
    assert "client_apps" in data


def test_debug_config(client):
    r = client.get("/debug/config")
    assert r.status_code == 200
    data = r.json()
    assert "auth_mode" in data
    assert "signing_kid" in data


def test_debug_decode(client):
    token_r = client.post(
        "/default/token",
        data={
            "grant_type": "password",
            "username": "alice",
            "password": "alice-pw",
            "resource": "api://serviceB",
        },
    )
    token = token_r.json()["access_token"]
    r = client.post("/debug/decode", json={"token": token})
    assert r.status_code == 200
    data = r.json()
    assert data["payload"]["sub"] == USERS["alice"].oid
    assert data["signature_validated_against_published_key"] is True


def test_debug_decode_wrong_sig(client):
    wrong_r = client.post(
        "/default/token/wrong-sig",
        data={
            "grant_type": "client_credentials",
            "client_id": "service-a",
            "client_secret": "serviceA-secret",
            "resource": "api://serviceB",
        },
    )
    token = wrong_r.json()["access_token"]
    r = client.post("/debug/decode", json={"token": token})
    assert r.status_code == 200
    assert r.json()["signature_validated_against_published_key"] is False


# ── Admin rotate-jwks ──────────────────────────────────────────────────────


def test_admin_rotate_wrong_token(client):
    r = client.post("/admin/rotate-jwks", headers={"X-Admin-Token": "wrong"})
    assert r.status_code == 403


def test_admin_rotate_changes_kid(client):
    before = client.get("/debug/config").json()["signing_kid"]
    client.post(
        "/admin/rotate-jwks", headers={"X-Admin-Token": "change-me-in-real-deployments"}
    )
    after = client.get("/debug/config").json()["signing_kid"]
    assert before != after


# ── Extra claims ───────────────────────────────────────────────────────────


def test_extra_claims_merged(client):
    r = client.post(
        "/default/token",
        data={
            "grant_type": "password",
            "username": "alice",
            "password": "alice-pw",
            "resource": "api://serviceB",
        },
    )
    payload = _decode_payload(r.json()["access_token"])
    assert payload.get("department") == "engineering"
    assert payload.get("cost_center") == "cc-1234"


# ── Unsupported grant ──────────────────────────────────────────────────────


def test_unsupported_grant_type(client):
    r = client.post("/default/token", data={"grant_type": "authorization_code"})
    assert r.status_code == 400


# ── Helpers ────────────────────────────────────────────────────────────────


def _decode_payload(token: str) -> dict:
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))
