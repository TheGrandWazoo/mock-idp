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
    assert len(keys) == 4  # RSA signing, EC signing, 2 RSA decoys
    ktypes = {k["kty"] for k in keys}
    assert "RSA" in ktypes
    assert "EC" in ktypes


def test_jwks_active_kid_matches_token(client):
    """The kid in the issued token must match the first key in /jwks."""
    token_r = client.post(
        "/default/token",
        data={"grant_type": "password", "username": "alice", "password": "alice-pw",
              "resource": "api://serviceB"},
    )
    token = token_r.json()["access_token"]
    header = json.loads(b64urlDecode_str(token.split(".")[0]))
    jwks_kids = [k["kid"] for k in client.get("/default/jwks").json()["keys"]]
    assert header["kid"] == jwks_kids[0]
    assert header["kid"] not in jwks_kids[1:]


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
    assert payload["aud"] == "01010101-1010-1010-1010-bbbbbbbbbbbb"
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
    assert _decode_payload(r.json()["access_token"])["aud"] == "01010101-1010-1010-1010-bbbbbbbbbbbb"


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
    assert _decode_payload(r.json()["access_token"])["aud"] == "01010101-1010-1010-1010-bbbbbbbbbbbb"


def test_user_aud_resolves_to_app_id_uuid(client):
    """User tokens carry the app_id UUID as aud; SP tokens carry the URI."""
    user_r = client.post(
        "/default/token",
        data={"grant_type": "password", "username": "alice", "password": "alice-pw",
              "resource": "api://serviceB"},
    )
    sp_r = client.post(
        "/default/token",
        data={"grant_type": "client_credentials", "client_id": "service-a",
              "client_secret": "serviceA-secret", "resource": "api://serviceB"},
    )
    user_aud = _decode_payload(user_r.json()["access_token"])["aud"]
    sp_aud = _decode_payload(sp_r.json()["access_token"])["aud"]
    assert user_aud == "01010101-1010-1010-1010-bbbbbbbbbbbb"  # app_id UUID
    assert sp_aud == "api://serviceB"                          # URI unchanged


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
    assert _decode_payload(r.json()["access_token"])["aud"] == "01010101-1010-1010-1010-bbbbbbbbbbbb"


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


# ── Per-issuer auth_mode ───────────────────────────────────────────────────


def test_per_issuer_strict_overrides_global_lax(client):
    """An issuer slug mapped to strict rejects unlisted audiences even when global is lax."""
    import mock_idp.config as m

    assert m.MODE == "lax"
    m.ISSUER_MODES["strict-slug"] = "strict"
    try:
        r = client.post(
            "/strict-slug/token",
            data={"grant_type": "password", "username": "bob", "password": "bob-pw",
                  "resource": "api://serviceC"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_target"
    finally:
        m.ISSUER_MODES.pop("strict-slug", None)


def test_per_issuer_lax_overrides_global_strict(client):
    """An issuer slug mapped to lax allows any audience even when global is strict."""
    import mock_idp.config as m

    original = m.MODE
    m.MODE = "strict"
    m.ISSUER_MODES["lax-slug"] = "lax"
    try:
        r = client.post(
            "/lax-slug/token",
            data={"grant_type": "password", "username": "alice", "password": "alice-pw",
                  "resource": "api://anything-goes"},
        )
        assert r.status_code == 200
    finally:
        m.MODE = original
        m.ISSUER_MODES.pop("lax-slug", None)


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


def test_x_test_fail_token(client):
    r = client.post(
        "/default/token",
        headers={"X-Test-Fail": "1"},
        data={"grant_type": "password", "username": "alice", "password": "alice-pw"},
    )
    assert r.status_code == 500


def test_x_test_fail_jwks(client):
    r = client.get("/default/jwks", headers={"X-Test-Fail": "1"})
    assert r.status_code == 500


def test_x_test_fail_discovery(client):
    r = client.get("/default/.well-known/openid-configuration", headers={"X-Test-Fail": "1"})
    assert r.status_code == 500


def test_x_test_delay_ms(client):
    import time
    start = time.monotonic()
    r = client.post(
        "/default/token",
        headers={"X-Test-Delay-Ms": "200"},
        data={"grant_type": "password", "username": "alice", "password": "alice-pw",
              "resource": "api://serviceB"},
    )
    elapsed_ms = (time.monotonic() - start) * 1000
    assert r.status_code == 200
    assert elapsed_ms >= 200


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


def test_unsigned_endpoint(client):
    r = client.post(
        "/default/token/unsigned",
        data={"grant_type": "client_credentials", "client_id": "service-a",
              "client_secret": "serviceA-secret", "resource": "api://serviceB"},
    )
    assert r.status_code == 200
    token = r.json()["access_token"]
    parts = token.split(".")
    assert len(parts) == 3
    assert parts[2] == ""  # empty signature
    header = json.loads(b64urlDecode_str(parts[0]))
    assert header["alg"] == "none"


def test_wrong_alg_endpoint(client):
    r = client.post(
        "/default/token/wrong-alg",
        data={"grant_type": "client_credentials", "client_id": "service-a",
              "client_secret": "serviceA-secret", "resource": "api://serviceB"},
    )
    assert r.status_code == 200
    token = r.json()["access_token"]
    parts = token.split(".")
    assert len(parts) == 3
    assert parts[2] != ""  # has a signature (just the wrong kind)
    header = json.loads(b64urlDecode_str(parts[0]))
    assert header["alg"] == "HS256"
    # kid must not appear — no kid means a gateway can't use kid-based RS256 verification
    assert "kid" not in header


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
    assert "signing_kids" in data
    assert isinstance(data["signing_kids"], dict)


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
    before = client.get("/debug/config").json()["signing_kids"]["default"]
    client.post(
        "/admin/rotate-jwks",
        params={"issuer": "default"},
        headers={"X-Admin-Token": "change-me-in-real-deployments"},
    )
    after = client.get("/debug/config").json()["signing_kids"]["default"]
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


# ── Introspection (RFC 7662) ───────────────────────────────────────────────


def _issue_token(client, *, username="alice", password="alice-pw", resource="api://serviceB"):
    r = client.post(
        "/default/token",
        data={"grant_type": "password", "username": username,
              "password": password, "resource": resource},
    )
    assert r.status_code == 200
    return r.json()["access_token"]


def test_introspect_active_token(client):
    token = _issue_token(client)
    r = client.post(
        "/default/introspect",
        data={"token": token, "client_id": "service-a", "client_secret": "serviceA-secret"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["active"] is True
    assert "sub" in data
    assert "exp" in data
    assert "iss" in data
    assert data["token_type"] == "Bearer"


def test_introspect_claims_passthrough(client):
    token = _issue_token(client)
    payload = _decode_payload(token)
    r = client.post(
        "/default/introspect",
        data={"token": token, "client_id": "service-a", "client_secret": "serviceA-secret"},
    )
    data = r.json()
    assert data["sub"] == payload["sub"]
    assert data["iss"] == payload["iss"]
    assert data["exp"] == payload["exp"]


def test_introspect_expired_token(client):
    # Manually backdate the exp claim to simulate expiry via a fresh token with X-Test-Expired.
    r = client.post(
        "/default/token",
        headers={"X-Test-Expired": "1"},
        data={"grant_type": "password", "username": "alice", "password": "alice-pw",
              "resource": "api://serviceB"},
    )
    expired_token = r.json()["access_token"]
    r = client.post(
        "/default/introspect",
        data={"token": expired_token, "client_id": "service-a", "client_secret": "serviceA-secret"},
    )
    assert r.status_code == 200
    assert r.json() == {"active": False}


def test_introspect_wrong_sig_token(client):
    r = client.post(
        "/default/token/wrong-sig",
        data={"grant_type": "client_credentials", "client_id": "service-a",
              "client_secret": "serviceA-secret", "resource": "api://serviceB"},
    )
    bad_token = r.json()["access_token"]
    r = client.post(
        "/default/introspect",
        data={"token": bad_token, "client_id": "service-a", "client_secret": "serviceA-secret"},
    )
    assert r.status_code == 200
    assert r.json() == {"active": False}


def test_introspect_malformed_token(client):
    r = client.post(
        "/default/introspect",
        data={"token": "not.a.token", "client_id": "service-a", "client_secret": "serviceA-secret"},
    )
    assert r.status_code == 200
    assert r.json() == {"active": False}


def test_introspect_invalid_caller(client):
    token = _issue_token(client)
    r = client.post(
        "/default/introspect",
        data={"token": token, "client_id": "service-a", "client_secret": "wrong-secret"},
    )
    assert r.status_code == 401


def test_introspect_missing_token(client):
    r = client.post(
        "/default/introspect",
        data={"client_id": "service-a", "client_secret": "serviceA-secret"},
    )
    assert r.status_code == 400


def test_introspect_discovery_includes_endpoint(client):
    r = client.get("/default/.well-known/openid-configuration")
    data = r.json()
    assert "introspection_endpoint" in data
    assert data["introspection_endpoint"].endswith("/default/introspect")


# ── Token Exchange (RFC 8693) ──────────────────────────────────────────────

_TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
_TOKEN_TYPE_AT = "urn:ietf:params:oauth:token-type:access_token"


def _issue_user_token(client, *, username="alice", password="alice-pw", resource="api://serviceB"):
    r = client.post(
        "/default/token",
        data={"grant_type": "password", "username": username,
              "password": password, "resource": resource},
    )
    assert r.status_code == 200
    return r.json()["access_token"]


def test_token_exchange_happy_path(client):
    user_token = _issue_user_token(client)
    r = client.post(
        "/default/token",
        data={
            "grant_type": _TOKEN_EXCHANGE,
            "subject_token": user_token,
            "subject_token_type": _TOKEN_TYPE_AT,
            "client_id": "service-a",
            "client_secret": "serviceA-secret",
            "audience": "api://serviceB",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert "access_token" in data
    assert data["token_type"] == "Bearer"
    assert data["issued_token_type"] == _TOKEN_TYPE_AT


def test_token_exchange_preserves_subject_identity(client):
    """sub and preferred_username from the inbound user token survive the exchange."""
    user_token = _issue_user_token(client)
    user_payload = _decode_payload(user_token)

    r = client.post(
        "/default/token",
        data={
            "grant_type": _TOKEN_EXCHANGE,
            "subject_token": user_token,
            "subject_token_type": _TOKEN_TYPE_AT,
            "client_id": "service-a",
            "client_secret": "serviceA-secret",
            "audience": "api://serviceB",
        },
    )
    exchanged = _decode_payload(r.json()["access_token"])
    assert exchanged["sub"] == user_payload["sub"]
    assert exchanged.get("preferred_username") == user_payload.get("preferred_username")
    assert exchanged["iss"] == user_payload["iss"]


def test_token_exchange_act_claim(client):
    """act.sub must identify the intermediary that performed the exchange."""
    user_token = _issue_user_token(client)
    r = client.post(
        "/default/token",
        data={
            "grant_type": _TOKEN_EXCHANGE,
            "subject_token": user_token,
            "subject_token_type": _TOKEN_TYPE_AT,
            "client_id": "service-a",
            "client_secret": "serviceA-secret",
            "audience": "api://serviceB",
        },
    )
    exchanged = _decode_payload(r.json()["access_token"])
    assert "act" in exchanged
    # act.sub identifies the intermediary (service-a's canonical client_id UUID)
    assert "sub" in exchanged["act"]


def test_token_exchange_new_audience(client):
    """The exchanged token carries the requested audience, not the inbound one."""
    user_token = _issue_user_token(client, resource="api://serviceB")
    r = client.post(
        "/default/token",
        data={
            "grant_type": _TOKEN_EXCHANGE,
            "subject_token": user_token,
            "subject_token_type": _TOKEN_TYPE_AT,
            "client_id": "service-a",
            "client_secret": "serviceA-secret",
            "audience": "api://serviceC",
        },
    )
    assert r.status_code == 200
    exchanged = _decode_payload(r.json()["access_token"])
    assert exchanged["aud"] == "api://serviceC"


def test_token_exchange_invalid_subject_token(client):
    r = client.post(
        "/default/token",
        data={
            "grant_type": _TOKEN_EXCHANGE,
            "subject_token": "not.a.valid.jwt",
            "subject_token_type": _TOKEN_TYPE_AT,
            "client_id": "service-a",
            "client_secret": "serviceA-secret",
            "audience": "api://serviceB",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_request"


def test_token_exchange_expired_subject_token(client):
    expired = client.post(
        "/default/token",
        headers={"X-Test-Expired": "1"},
        data={"grant_type": "password", "username": "alice", "password": "alice-pw",
              "resource": "api://serviceB"},
    ).json()["access_token"]
    r = client.post(
        "/default/token",
        data={
            "grant_type": _TOKEN_EXCHANGE,
            "subject_token": expired,
            "subject_token_type": _TOKEN_TYPE_AT,
            "client_id": "service-a",
            "client_secret": "serviceA-secret",
            "audience": "api://serviceB",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_request"


def test_token_exchange_invalid_caller(client):
    user_token = _issue_user_token(client)
    r = client.post(
        "/default/token",
        data={
            "grant_type": _TOKEN_EXCHANGE,
            "subject_token": user_token,
            "subject_token_type": _TOKEN_TYPE_AT,
            "client_id": "service-a",
            "client_secret": "wrong-secret",
            "audience": "api://serviceB",
        },
    )
    assert r.status_code == 401


def test_token_exchange_discovery_includes_grant(client):
    r = client.get("/default/.well-known/openid-configuration")
    assert _TOKEN_EXCHANGE in r.json()["grant_types_supported"]


# ── Unsupported grant ──────────────────────────────────────────────────────


def test_unsupported_grant_type(client):
    r = client.post("/default/token", data={"grant_type": "authorization_code"})
    assert r.status_code == 400


# ── Per-issuer signing key isolation ──────────────────────────────────────


def test_per_issuer_jwks_distinct_kids(client):
    """Two different issuer paths must publish completely different key sets."""
    kids_a = {k["kid"] for k in client.get("/default/jwks").json()["keys"]}
    kids_b = {k["kid"] for k in client.get("/other-issuer/jwks").json()["keys"]}
    assert kids_a.isdisjoint(kids_b)


def test_token_only_verifies_against_own_issuer_jwks(client):
    """A token from /default must not verify against /other-issuer's published keys."""
    from mock_idp.keys import get_jwks_keys
    from mock_idp.tokens import verify_token

    token = client.post(
        "/default/token",
        data={"grant_type": "password", "username": "alice", "password": "alice-pw",
              "resource": "api://serviceB"},
    ).json()["access_token"]

    assert verify_token(token, get_jwks_keys("default")) is not None
    assert verify_token(token, get_jwks_keys("other-issuer")) is None


def test_admin_rotate_single_issuer_does_not_affect_others(client):
    """Rotating one issuer's key must leave other issuers' kids unchanged."""
    kids_before = client.get("/debug/config").json()["signing_kids"]
    client.post(
        "/admin/rotate-jwks",
        params={"issuer": "default"},
        headers={"X-Admin-Token": "change-me-in-real-deployments"},
    )
    kids_after = client.get("/debug/config").json()["signing_kids"]
    assert kids_after["default"] != kids_before["default"]
    for issuer, kid in kids_before.items():
        if issuer != "default":
            assert kids_after[issuer] == kid


# ── Webhook on token issuance ──────────────────────────────────────────────


def test_webhook_fires_on_password_grant(client):
    """A configured webhook receives a payload when a password-grant token is issued."""
    from unittest.mock import patch

    import mock_idp.config as m
    from mock_idp.models import WebhookConfig

    captured = []

    async def fake_deliver(url, body, timeout):
        captured.append(body)

    m.WEBHOOKS.append(WebhookConfig(url="http://wh.test/events"))
    try:
        with patch("mock_idp.webhooks._deliver", side_effect=fake_deliver):
            r = client.post(
                "/default/token",
                data={"grant_type": "password", "username": "alice",
                      "password": "alice-pw", "resource": "api://serviceB"},
            )
        assert r.status_code == 200
        assert len(captured) == 1
        body = captured[0]
        assert body["event"] == "token_issued"
        assert body["grant_type"] == "password"
        assert body["issuer"] == "default"
        assert "claims" in body
        assert body["claims"]["sub"] == USERS["alice"].oid
    finally:
        m.WEBHOOKS[:] = [h for h in m.WEBHOOKS if h.url != "http://wh.test/events"]


def test_webhook_fires_on_client_credentials(client):
    """Webhook fires for client_credentials grant with correct claims."""
    from unittest.mock import patch

    import mock_idp.config as m
    from mock_idp.models import WebhookConfig

    captured = []

    async def fake_deliver(url, body, timeout):
        captured.append(body)

    m.WEBHOOKS.append(WebhookConfig(url="http://wh.test/events"))
    try:
        with patch("mock_idp.webhooks._deliver", side_effect=fake_deliver):
            r = client.post(
                "/default/token",
                data={"grant_type": "client_credentials", "client_id": "service-a",
                      "client_secret": "serviceA-secret", "resource": "api://serviceB"},
            )
        assert r.status_code == 200
        assert len(captured) == 1
        assert captured[0]["grant_type"] == "client_credentials"
        assert captured[0]["claims"]["aud"] == "api://serviceB"
    finally:
        m.WEBHOOKS[:] = [h for h in m.WEBHOOKS if h.url != "http://wh.test/events"]


def test_webhook_not_fired_when_unconfigured(client):
    """No webhook calls when WEBHOOKS list is empty."""
    from unittest.mock import patch

    import mock_idp.config as m

    assert m.WEBHOOKS == []  # baseline from config.example.yaml

    with patch("mock_idp.webhooks._deliver") as mock_deliver:
        client.post(
            "/default/token",
            data={"grant_type": "password", "username": "alice",
                  "password": "alice-pw", "resource": "api://serviceB"},
        )
    mock_deliver.assert_not_called()


def test_webhook_failure_does_not_break_token_issuance(client):
    """Token is issued normally even when the webhook endpoint is unreachable."""
    import mock_idp.config as m
    from mock_idp.models import WebhookConfig

    m.WEBHOOKS.append(WebhookConfig(url="http://does-not-exist.invalid/events"))
    try:
        r = client.post(
            "/default/token",
            data={"grant_type": "password", "username": "alice",
                  "password": "alice-pw", "resource": "api://serviceB"},
        )
        assert r.status_code == 200
        assert "access_token" in r.json()
    finally:
        m.WEBHOOKS[:] = [h for h in m.WEBHOOKS if "does-not-exist" not in h.url]


def test_webhook_event_filter_respected(client):
    """A webhook configured for an unknown event name never fires on token_issued."""
    from unittest.mock import patch

    import mock_idp.config as m
    from mock_idp.models import WebhookConfig

    m.WEBHOOKS.append(WebhookConfig(url="http://wh.test/events", events=["other_event"]))
    try:
        with patch("mock_idp.webhooks._deliver") as mock_deliver:
            client.post(
                "/default/token",
                data={"grant_type": "password", "username": "alice",
                      "password": "alice-pw", "resource": "api://serviceB"},
            )
        mock_deliver.assert_not_called()
    finally:
        m.WEBHOOKS[:] = [h for h in m.WEBHOOKS if h.url != "http://wh.test/events"]


# ── Configurable signing algorithm (RS256 / ES256) ────────────────────────


def test_es256_token_has_correct_alg_header(client):
    """service-b is configured with signing_alg: ES256 — alg header must reflect that."""
    r = client.post(
        "/default/token",
        data={"grant_type": "client_credentials", "client_id": "service-b",
              "client_secret": "serviceB-secret", "resource": "api://serviceC"},
    )
    assert r.status_code == 200
    header = json.loads(b64urlDecode_str(r.json()["access_token"].split(".")[0]))
    assert header["alg"] == "ES256"


def test_es256_token_kid_matches_ec_key_in_jwks(client):
    """The kid in an ES256 token must match the EC key published in /jwks."""
    r = client.post(
        "/default/token",
        data={"grant_type": "client_credentials", "client_id": "service-b",
              "client_secret": "serviceB-secret", "resource": "api://serviceC"},
    )
    token = r.json()["access_token"]
    header = json.loads(b64urlDecode_str(token.split(".")[0]))
    token_kid = header["kid"]

    jwks_keys = client.get("/default/jwks").json()["keys"]
    ec_keys = [k for k in jwks_keys if k["kty"] == "EC"]
    assert any(k["kid"] == token_kid for k in ec_keys)


def test_es256_token_verifies_against_published_jwks(client):
    """An ES256 token must verify successfully using the published JWKS."""
    from mock_idp.keys import get_jwks_keys
    from mock_idp.tokens import verify_token

    r = client.post(
        "/default/token",
        data={"grant_type": "client_credentials", "client_id": "service-b",
              "client_secret": "serviceB-secret", "resource": "api://serviceC"},
    )
    token = r.json()["access_token"]
    claims = verify_token(token, get_jwks_keys("default"))
    assert claims is not None
    assert claims["appid"] == "02020202-2020-2020-2020-bbbbbbbbbbbb"


def test_rs256_identity_unaffected_by_es256_config(client):
    """service-a uses default RS256 — adding ES256 elsewhere must not affect it."""
    r = client.post(
        "/default/token",
        data={"grant_type": "client_credentials", "client_id": "service-a",
              "client_secret": "serviceA-secret", "resource": "api://serviceB"},
    )
    token = r.json()["access_token"]
    header = json.loads(b64urlDecode_str(token.split(".")[0]))
    assert header["alg"] == "RS256"


def test_discovery_advertises_both_algorithms(client):
    r = client.get("/default/.well-known/openid-configuration")
    algs = r.json()["id_token_signing_alg_values_supported"]
    assert "RS256" in algs
    assert "ES256" in algs


# ── Helpers ────────────────────────────────────────────────────────────────


def _decode_payload(token: str) -> dict:
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


def b64urlDecode_str(s: str) -> str:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s).decode()
