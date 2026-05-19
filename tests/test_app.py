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


# ── Realm roles ────────────────────────────────────────────────────────────


def test_identity_realm_roles_always_in_token(client):
    """identity.realm_roles appear in the token regardless of audience."""
    import mock_idp.config as m
    alice = m.USERS["alice"]
    original = list(alice.realm_roles)
    alice.realm_roles = ["Global.Reader"]
    try:
        r = client.post(
            "/default/token",
            data={"grant_type": "password", "username": "alice", "password": "alice-pw",
                  "resource": "api://serviceB"},
        )
        assert r.status_code == 200
        payload = _decode_payload(r.json()["access_token"])
        assert "Global.Reader" in payload["roles"]
    finally:
        alice.realm_roles = original


def test_tenant_realm_roles_applied_to_all_identities(client):
    """_tenant_realm_roles are injected on all identities and appear in every token."""
    import mock_idp.config as m
    alice = m.USERS["alice"]
    original = list(alice._tenant_realm_roles)
    alice._tenant_realm_roles = ["offline_access"]
    try:
        r = client.post(
            "/default/token",
            data={"grant_type": "password", "username": "alice", "password": "alice-pw",
                  "resource": "api://serviceB"},
        )
        assert r.status_code == 200
        payload = _decode_payload(r.json()["access_token"])
        assert "offline_access" in payload["roles"]
    finally:
        alice._tenant_realm_roles = original


def test_realm_roles_merge_order_and_dedup(client):
    """Merge order: tenant → identity → audience; duplicates dropped (first wins)."""
    import mock_idp.config as m
    alice = m.USERS["alice"]
    orig_tenant = list(alice._tenant_realm_roles)
    orig_realm = list(alice.realm_roles)
    alice._tenant_realm_roles = ["shared-role", "tenant-only"]
    alice.realm_roles = ["shared-role", "identity-only"]
    try:
        r = client.post(
            "/default/token",
            data={"grant_type": "password", "username": "alice", "password": "alice-pw",
                  "resource": "api://serviceB"},
        )
        assert r.status_code == 200
        payload = _decode_payload(r.json()["access_token"])
        roles = payload["roles"]
        # shared-role appears exactly once
        assert roles.count("shared-role") == 1
        # order: tenant roles come before identity roles
        assert roles.index("tenant-only") < roles.index("identity-only")
        assert "tenant-only" in roles
        assert "identity-only" in roles
    finally:
        alice._tenant_realm_roles = orig_tenant
        alice.realm_roles = orig_realm


def test_realm_roles_on_service_principal(client):
    """realm_roles on a service principal appear in the client_credentials token."""
    import mock_idp.config as m
    sp = m.SERVICE_PRINCIPALS["service-a"]
    original = list(sp.realm_roles)
    sp.realm_roles = ["Automation.Base"]
    try:
        r = client.post(
            "/default/token",
            data={"grant_type": "client_credentials", "client_id": "service-a",
                  "client_secret": "serviceA-secret", "resource": "api://serviceB"},
        )
        assert r.status_code == 200
        payload = _decode_payload(r.json()["access_token"])
        assert "Automation.Base" in payload["roles"]
    finally:
        sp.realm_roles = original


# ── Secret references (from_env / from_file) ──────────────────────────────


def test_secret_from_env_resolves_password(tmp_path):
    """from_env on a user password is resolved before token issuance."""
    import os
    from mock_idp.store.yaml_store import _resolve_secrets

    os.environ["_TEST_PW"] = "env-resolved-password"
    raw = {
        "tenants": {
            "t1": {
                "users": {
                    "testuser": {"password": {"from_env": "_TEST_PW"}}
                }
            }
        }
    }
    try:
        resolved = _resolve_secrets(raw, tmp_path / "config.yaml")
        assert resolved["tenants"]["t1"]["users"]["testuser"]["password"] == "env-resolved-password"
    finally:
        os.environ.pop("_TEST_PW", None)


def test_secret_from_file_resolves_secret(tmp_path):
    """from_file on a service principal secret is resolved before token issuance."""
    from mock_idp.store.yaml_store import _resolve_secrets

    secret_file = tmp_path / "sp-secret"
    secret_file.write_text("  file-resolved-secret  \n")  # leading/trailing whitespace trimmed

    raw = {
        "tenants": {
            "t1": {
                "service_principals": {
                    "svc": {"secret": {"from_file": str(secret_file)}}
                }
            }
        }
    }
    resolved = _resolve_secrets(raw, tmp_path / "config.yaml")
    assert resolved["tenants"]["t1"]["service_principals"]["svc"]["secret"] == "file-resolved-secret"


def test_secret_plain_string_unchanged(tmp_path):
    """Plain string secrets are passed through unchanged."""
    from mock_idp.store.yaml_store import _resolve_secrets

    raw = {"admin_token": "my-plain-token"}
    resolved = _resolve_secrets(raw, tmp_path / "config.yaml")
    assert resolved["admin_token"] == "my-plain-token"


def test_secret_missing_env_var_raises(tmp_path):
    """Referencing an unset env var raises ValueError with a clear message."""
    import os
    from mock_idp.store.yaml_store import _resolve_secrets

    os.environ.pop("_TEST_MISSING_VAR", None)
    raw = {"admin_token": {"from_env": "_TEST_MISSING_VAR"}}
    with pytest.raises(ValueError, match="_TEST_MISSING_VAR"):
        _resolve_secrets(raw, tmp_path / "config.yaml")


def test_secret_missing_file_raises(tmp_path):
    """Referencing a non-existent file raises ValueError with a clear message."""
    from mock_idp.store.yaml_store import _resolve_secrets

    raw = {"admin_token": {"from_file": str(tmp_path / "no-such-file")}}
    with pytest.raises(ValueError, match="no-such-file"):
        _resolve_secrets(raw, tmp_path / "config.yaml")


# ── X-Override-Roles test header ───────────────────────────────────────────


def test_override_roles_password_grant(client):
    """X-Override-Roles replaces resolved roles in a password-grant token."""
    r = client.post(
        "/default/token",
        headers={"X-Override-Roles": "custom-role,another-role"},
        data={"grant_type": "password", "username": "alice", "password": "alice-pw",
              "resource": "api://serviceB"},
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert payload["roles"] == ["custom-role", "another-role"]


def test_override_roles_client_credentials(client):
    """X-Override-Roles replaces resolved roles in a client_credentials token."""
    r = client.post(
        "/default/token",
        headers={"X-Override-Roles": "svc-role"},
        data={"grant_type": "client_credentials", "client_id": "service-a",
              "client_secret": "serviceA-secret", "resource": "api://serviceB"},
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert payload["roles"] == ["svc-role"]


def test_override_roles_empty_removes_claim(client):
    """X-Override-Roles with an empty value should produce no roles claim."""
    r = client.post(
        "/default/token",
        headers={"X-Override-Roles": ""},
        data={"grant_type": "password", "username": "alice", "password": "alice-pw",
              "resource": "api://serviceB"},
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert payload.get("roles", []) == []


def test_override_roles_absent_keeps_configured_roles(client):
    """Without X-Override-Roles the token carries the identity's resolved roles."""
    from mock_idp.tokens import resolve_roles
    expected = sorted(resolve_roles("alice", USERS["alice"], "api://serviceB"))
    r = client.post(
        "/default/token",
        data={"grant_type": "password", "username": "alice", "password": "alice-pw",
              "resource": "api://serviceB"},
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert sorted(payload.get("roles", [])) == expected


# ── Token Audit Log ────────────────────────────────────────────────────────

_ADMIN_TOKEN = "change-me-in-real-deployments"


def test_audit_endpoint_requires_admin_token(client):
    r = client.get("/admin/audit")
    assert r.status_code == 403

    r = client.get("/admin/audit", headers={"x-admin-token": "wrong"})
    assert r.status_code == 403


def test_audit_yaml_backend_returns_empty_list(client):
    """YAML backend always returns an empty audit log."""
    r = client.get("/admin/audit", headers={"x-admin-token": _ADMIN_TOKEN})
    assert r.status_code == 200
    data = r.json()
    assert data["events"] == []
    assert data["next_cursor"] is None


def test_audit_limit_and_cursor_params_accepted(client):
    """Endpoint accepts limit and cursor query params without error."""
    r = client.get(
        "/admin/audit",
        headers={"x-admin-token": _ADMIN_TOKEN},
        params={"limit": 10, "cursor": 0},
    )
    assert r.status_code == 200


def test_audit_limit_validation(client):
    """limit must be 1–200; out-of-range values return 422."""
    r = client.get(
        "/admin/audit",
        headers={"x-admin-token": _ADMIN_TOKEN},
        params={"limit": 0},
    )
    assert r.status_code == 422

    r = client.get(
        "/admin/audit",
        headers={"x-admin-token": _ADMIN_TOKEN},
        params={"limit": 201},
    )
    assert r.status_code == 422


def test_audit_write_does_not_break_token_issuance(client):
    """write_token_event failure must not prevent token issuance."""
    from unittest.mock import AsyncMock, patch

    import mock_idp.config as m

    broken = AsyncMock(side_effect=RuntimeError("simulated DB failure"))
    with patch.object(m.store, "write_token_event", broken):
        r = client.post(
            "/default/token",
            data={"grant_type": "password", "username": "alice",
                  "password": "alice-pw", "resource": "api://serviceB"},
        )
    assert r.status_code == 200
    assert "access_token" in r.json()


def test_audit_write_called_on_password_grant(client):
    """write_token_event is awaited on every successful password grant."""
    from unittest.mock import AsyncMock, patch

    import mock_idp.config as m

    spy = AsyncMock()
    with patch.object(m.store, "write_token_event", spy):
        r = client.post(
            "/default/token",
            data={"grant_type": "password", "username": "alice",
                  "password": "alice-pw", "resource": "api://serviceB"},
        )
    assert r.status_code == 200
    spy.assert_awaited_once()
    event = spy.call_args[0][0]
    assert event.get("grant_type") == "password"
    assert "sub" in event


def test_audit_write_called_on_client_credentials(client):
    """write_token_event is awaited on every successful client_credentials grant."""
    from unittest.mock import AsyncMock, patch

    import mock_idp.config as m

    spy = AsyncMock()
    with patch.object(m.store, "write_token_event", spy):
        r = client.post(
            "/default/token",
            data={"grant_type": "client_credentials", "client_id": "service-a",
                  "client_secret": "serviceA-secret", "resource": "api://serviceB"},
        )
    assert r.status_code == 200
    spy.assert_awaited_once()
    event = spy.call_args[0][0]
    assert event.get("grant_type") == "client_credentials"


def test_audit_write_called_on_token_exchange(client):
    """write_token_event is awaited on every successful token-exchange."""
    from unittest.mock import AsyncMock, patch

    import mock_idp.config as m

    user_token = client.post(
        "/default/token",
        data={"grant_type": "password", "username": "alice",
              "password": "alice-pw", "resource": "api://serviceB"},
    ).json()["access_token"]

    spy = AsyncMock()
    with patch.object(m.store, "write_token_event", spy):
        r = client.post(
            "/default/token",
            data={
                "grant_type": _TOKEN_EXCHANGE,
                "subject_token": user_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "client_id": "service-a",
                "client_secret": "serviceA-secret",
                "audience": "api://serviceB",
            },
        )
    assert r.status_code == 200
    spy.assert_awaited_once()
    event = spy.call_args[0][0]
    assert event.get("grant_type") == _TOKEN_EXCHANGE


def test_audit_multi_org_isolation():
    """Stores with different org_ids cannot read each other's data."""
    import asyncio
    from unittest.mock import AsyncMock

    async def run():
        from mock_idp.store.yaml_store import YamlIdentityStore

        store_a = YamlIdentityStore.__new__(YamlIdentityStore)
        store_b = YamlIdentityStore.__new__(YamlIdentityStore)

        events_a: list[dict] = [{"org": "a", "id": 1}]
        events_b: list[dict] = [{"org": "b", "id": 2}]

        store_a.query_audit = AsyncMock(return_value=(events_a, None))
        store_b.query_audit = AsyncMock(return_value=(events_b, None))

        result_a, _ = await store_a.query_audit(limit=50, cursor=0)
        result_b, _ = await store_b.query_audit(limit=50, cursor=0)

        assert result_a == events_a
        assert result_b == events_b
        assert result_a != result_b

    asyncio.run(run())


# ── Rate limiting (slowapi) ───────────────────────────────────────────────


def test_rate_limit_disabled_by_default(client):
    """No rate limiting by default — healthz has no rate_limit key."""
    r = client.get("/healthz")
    assert r.status_code == 200
    assert "rate_limit" not in r.json()


def test_rate_limit_enabled_shows_in_healthz():
    """MOCK_IDP_RATE_LIMIT=10/minute → healthz includes rate_limit info."""
    import importlib
    from unittest.mock import patch

    with patch.dict(os.environ, {"MOCK_IDP_RATE_LIMIT": "10/minute"}):
        import mock_idp.ratelimit as rl_mod
        importlib.reload(rl_mod)
        import mock_idp.routers.oidc as oidc_mod
        importlib.reload(oidc_mod)
        import mock_idp.main as main_mod
        importlib.reload(main_mod)
        from fastapi.testclient import TestClient as _TC
        with _TC(main_mod.app) as c:
            r = c.get("/healthz")
        assert r.status_code == 200
        data = r.json()
        assert data.get("rate_limit", {}).get("enabled") is True
        assert data.get("rate_limit", {}).get("limit") == "10/minute"

    for mod in ("mock_idp.ratelimit", "mock_idp.routers.oidc", "mock_idp.main"):
        importlib.reload(__import__(mod, fromlist=[""]))


def test_rate_limit_429_when_exceeded():
    """Requests beyond the limit return 429 with Retry-After header."""
    import importlib
    from unittest.mock import patch

    with patch.dict(os.environ, {"MOCK_IDP_RATE_LIMIT": "2/minute"}):
        import mock_idp.ratelimit as rl_mod
        importlib.reload(rl_mod)
        import mock_idp.routers.oidc as oidc_mod
        importlib.reload(oidc_mod)
        import mock_idp.main as main_mod
        importlib.reload(main_mod)
        from fastapi.testclient import TestClient as _TC
        with _TC(main_mod.app) as c:
            for _ in range(2):
                c.post(
                    "/default/token",
                    data={"grant_type": "password", "username": "alice",
                          "password": "alice-pw", "resource": "api://serviceB"},
                )
            r = c.post(
                "/default/token",
                data={"grant_type": "password", "username": "alice",
                      "password": "alice-pw", "resource": "api://serviceB"},
            )
        assert r.status_code == 429
        assert "retry-after" in {k.lower() for k in r.headers}

    for mod in ("mock_idp.ratelimit", "mock_idp.routers.oidc", "mock_idp.main"):
        importlib.reload(__import__(mod, fromlist=[""]))


# ── Prometheus /metrics endpoint ────────────────────────────────────────────


def test_metrics_disabled_by_default(client):
    """/metrics returns 404 when MOCK_IDP_METRICS_ENABLED is not set."""
    r = client.get("/metrics")
    assert r.status_code == 404


def test_metrics_enabled_returns_prometheus_text():
    """When instrumented, /metrics returns 200 with Prometheus text format."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from prometheus_fastapi_instrumentator import Instrumentator

    test_app = FastAPI()
    Instrumentator().instrument(test_app).expose(test_app)

    with TestClient(test_app) as c:
        r = c.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers.get("content-type", "")


def test_metrics_env_var_gates_instrumentation():
    """MOCK_IDP_METRICS_ENABLED=true causes /metrics route to be registered."""
    import importlib
    from unittest.mock import patch

    with patch.dict(os.environ, {"MOCK_IDP_METRICS_ENABLED": "true"}):
        import mock_idp.main as main_mod
        importlib.reload(main_mod)
        from fastapi.testclient import TestClient as _TC
        with _TC(main_mod.app) as c:
            r = c.get("/metrics")
        assert r.status_code == 200

    importlib.reload(main_mod)


# ── Multi-provider claim shapes ────────────────────────────────────────────


def test_okta_user_token_claim_shape(client):
    """Okta provider: sub=email, uid=oid, cid, scp string, no tid/ver."""
    r = client.post(
        "/okta-tenant/token",
        data={
            "grant_type": "password",
            "username": "carol",
            "password": "carol-pw",
            "resource": "api://okta-api",
            "client_id": "okta-client-id",
        },
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert payload["sub"] == "carol@example.com"
    assert payload["uid"] == "33333333-3333-3333-3333-aaaaaaaaaaaa"
    assert payload["cid"] == "okta-client-id"
    assert isinstance(payload["scp"], str)
    assert "groups" in payload
    assert "tid" not in payload
    assert "ver" not in payload


def test_okta_sp_token_claim_shape(client):
    """Okta provider: SP token has sub=client_id, cid, scp string."""
    r = client.post(
        "/okta-tenant/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "okta-client-id",
            "client_secret": "okta-service-secret",
            "resource": "api://okta-api",
        },
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert payload["sub"] == "okta-client-id"
    assert payload["cid"] == "okta-client-id"
    assert isinstance(payload["scp"], str)
    assert "tid" not in payload
    assert "ver" not in payload


def test_cognito_user_token_claim_shape(client):
    """Cognito provider: token_use=access, cognito:username, cognito:groups, scope string."""
    r = client.post(
        "/cognito-tenant/token",
        data={
            "grant_type": "password",
            "username": "dave",
            "password": "dave-pw",
            "resource": "api://cognito-api",
            "client_id": "cognito-client-id",
        },
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert payload["token_use"] == "access"
    assert payload["cognito:username"] == "dave"
    assert isinstance(payload["cognito:groups"], list)
    assert isinstance(payload["scope"], str)
    assert payload["client_id"] == "cognito-client-id"
    assert "tid" not in payload
    assert "ver" not in payload


def test_cognito_sp_token_claim_shape(client):
    """Cognito provider: SP token has token_use=access, client_id, scope string."""
    r = client.post(
        "/cognito-tenant/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "cognito-client-id",
            "client_secret": "cognito-service-secret",
            "resource": "api://cognito-api",
        },
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert payload["token_use"] == "access"
    assert payload["client_id"] == "cognito-client-id"
    assert isinstance(payload["scope"], str)
    assert "tid" not in payload


def test_keycloak_user_token_claim_shape(client):
    """Keycloak provider: realm_access.roles, resource_access, preferred_username, email, azp."""
    r = client.post(
        "/keycloak-tenant/token",
        data={
            "grant_type": "password",
            "username": "eve",
            "password": "eve-pw",
            "resource": "api://keycloak-api",
            "client_id": "keycloak-client-id",
        },
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert payload["preferred_username"] == "eve"
    assert payload["email"] == "eve@example.com"
    assert "roles" in payload["realm_access"]
    assert isinstance(payload["realm_access"]["roles"], list)
    assert payload["azp"] == "keycloak-client-id"
    assert "keycloak-client-id" in payload["resource_access"]
    assert "tid" not in payload
    assert "ver" not in payload


def test_keycloak_sp_token_claim_shape(client):
    """Keycloak provider: SP token has realm_access.roles, resource_access, azp."""
    r = client.post(
        "/keycloak-tenant/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "keycloak-client-id",
            "client_secret": "keycloak-service-secret",
            "resource": "api://keycloak-api",
        },
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert payload["azp"] == "keycloak-client-id"
    assert "roles" in payload["realm_access"]
    assert "keycloak-client-id" in payload["resource_access"]
    assert "tid" not in payload


def test_provider_isolation_entra_id_unaffected(client):
    """Entra ID tenant still produces oid, tid, ver — unaffected by new providers."""
    r = client.post(
        "/default/token",
        data={"grant_type": "password", "username": "alice", "password": "alice-pw",
              "resource": "api://serviceB"},
    )
    assert r.status_code == 200
    payload = _decode_payload(r.json()["access_token"])
    assert "oid" in payload
    assert "tid" in payload
    assert "ver" in payload


# ── Helpers ────────────────────────────────────────────────────────────────


def _decode_payload(token: str) -> dict:
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


def b64urlDecode_str(s: str) -> str:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s).decode()
