"""
Gateway integration tests: mock-idp → APISIX → go-httpbin.

Run the stack first:
  docker compose -f docker-compose.integration.yml up -d --wait
  uv run pytest tests/integration/ -v
"""

import httpx

APISIX_URL = "http://localhost:19080"


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── IT001-002: password-grant tokens ──────────────────────────────────────────

class TestPasswordGrant:
    def test_it001_user_token_alpha_route(self, alpha_user_token):
        """IT001: password grant → /alpha/ → 200"""
        r = httpx.get(f"{APISIX_URL}/alpha/get", headers=_bearer(alpha_user_token))
        assert r.status_code == 200

    def test_it002_user_token_beta_route(self, beta_user_token):
        """IT002: password grant → /beta/ → 200"""
        r = httpx.get(f"{APISIX_URL}/beta/get", headers=_bearer(beta_user_token))
        assert r.status_code == 200


# ── IT003: client_credentials grant ───────────────────────────────────────────

class TestClientCredentials:
    def test_it003_cc_token_alpha_route(self, alpha_cc_token):
        """IT003: client_credentials → /alpha/ → 200"""
        r = httpx.get(f"{APISIX_URL}/alpha/get", headers=_bearer(alpha_cc_token))
        assert r.status_code == 200


# ── IT004-007: auth failure cases ─────────────────────────────────────────────

class TestAuthFailures:
    def test_it004_no_token(self):
        """IT004: missing Authorization header → 401"""
        r = httpx.get(f"{APISIX_URL}/alpha/get")
        assert r.status_code == 401

    def test_it005_malformed_token(self):
        """IT005: non-JWT bearer value → 401"""
        r = httpx.get(f"{APISIX_URL}/alpha/get", headers={"Authorization": "Bearer not.a.jwt"})
        assert r.status_code == 401

    def test_it006_expired_token(self, expired_token):
        """IT006: expired token (token_lifetime_seconds=1, slept 2s) → 401"""
        r = httpx.get(f"{APISIX_URL}/alpha/get", headers=_bearer(expired_token))
        assert r.status_code == 401

    def test_it007_tampered_signature(self, alpha_user_token):
        """IT007: valid header.payload but forged signature → 401"""
        header_payload, _ = alpha_user_token.rsplit(".", 1)
        forged = header_payload + "." + "A" * 43  # replace sig segment
        r = httpx.get(f"{APISIX_URL}/alpha/get", headers=_bearer(forged))
        assert r.status_code == 401


# ── IT008-009: audience enforcement ───────────────────────────────────────────

class TestAudienceEnforcement:
    def test_it008_alpha_token_on_beta_route(self, alpha_cc_token):
        """IT008: aud=api://alpha-api token on /beta/ → 401 (wrong audience)"""
        r = httpx.get(f"{APISIX_URL}/beta/get", headers=_bearer(alpha_cc_token))
        assert r.status_code == 401

    def test_it009_beta_token_on_alpha_route(self, beta_cc_token):
        """IT009: aud=api://beta-api token on /alpha/ → 401 (wrong audience)"""
        r = httpx.get(f"{APISIX_URL}/alpha/get", headers=_bearer(beta_cc_token))
        assert r.status_code == 401


# ── IT010: identity header forwarding ─────────────────────────────────────────

class TestHeaderForwarding:
    def test_it010_userinfo_header_forwarded(self, alpha_user_token):
        """IT010: APISIX forwards X-Userinfo header with decoded JWT claims"""
        r = httpx.get(f"{APISIX_URL}/alpha/headers", headers=_bearer(alpha_user_token))
        assert r.status_code == 200
        headers_lower = {k.lower(): v for k, v in r.json().get("headers", {}).items()}
        # openid-connect plugin sets X-Userinfo (base64url-encoded claims) by default
        assert "x-userinfo" in headers_lower, (
            f"Expected X-Userinfo in upstream headers. Got: {list(headers_lower.keys())}"
        )
