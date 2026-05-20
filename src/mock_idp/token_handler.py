"""Shared token-issuance logic consumed by both the flat OIDC router and the
hosted slug router.

Callers build a ``TokenContext`` that bundles per-request config (identity
dicts, key resolver, store, ISS base URL) then call the appropriate handler.
This eliminates duplication between the two routers while keeping each
router thin.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from joserfc.jwk import ECKey, RSAKey

from .providers import get_provider
from .tokens import (
    apply_overrides,
    apply_roles_override,
    apply_test_hooks,
    check_audience,
    make_unsigned_token,
    make_wrong_alg_token,
    omit,
    resolve_aud,
    resolve_expiry,
    resolve_roles,
    resolve_shape,
    sign,
    verify_token,
)
from .webhooks import fire_webhooks

_log = logging.getLogger(__name__)

_JWK = RSAKey | ECKey


class KeyResolver(Protocol):
    """Pluggable key provider — global or per-org."""

    def signing_key_for_alg(self, issuer: str, alg: str) -> _JWK: ...
    def alt_key_for_alg(self, issuer: str, alg: str) -> _JWK: ...
    def jwks_keys(self, issuer: str) -> list[_JWK]: ...
    def signing_public_key_pem(self, issuer: str) -> bytes: ...


@dataclass
class TokenContext:
    """All config needed to handle one request; shared between flat and hosted routes."""

    iss_base: str
    users: dict
    service_principals: dict
    mode: str
    issuer_modes: dict
    store: Any
    keys: KeyResolver


# ── Grant handlers ─────────────────────────────────────────────────────────

async def handle_token(issuer: str, request: Any, ctx: TokenContext) -> dict:
    """Process a POST /{issuer}/token request using the provided context."""
    form = dict(await request.form())
    headers = {k.lower(): v for k, v in request.headers.items()}
    await apply_test_hooks(headers)
    grant_type = form.get("grant_type")
    aud = resolve_aud(form)
    effective_mode = ctx.issuer_modes.get(issuer) or ctx.mode

    signing_alg = "RS256"

    if grant_type == "password":
        user_key = form.get("username") or ""
        user = ctx.users.get(user_key)
        if not user or user.password != form.get("password"):
            raise HTTPException(401, "invalid_grant")
        check_audience(user_key, user, aud, mode=effective_mode)
        shape = resolve_shape(user.token_version, form, headers.get("x-token-shape"))
        expires_in = resolve_expiry(user.token_lifetime_seconds, headers)
        roles = apply_roles_override(resolve_roles(user_key, user, aud), headers)
        provider = get_provider(user._provider)
        claims = provider.user_claims(issuer, user, aud, shape, expires_in, roles, form.get("client_id"))
        signing_alg = user.signing_alg

    elif grant_type == "client_credentials":
        sp_key = form.get("client_id") or ""
        sp = ctx.service_principals.get(sp_key)
        if not sp or sp.secret != form.get("client_secret"):
            raise HTTPException(401, "invalid_client")
        check_audience(sp_key, sp, aud, mode=effective_mode)
        shape = resolve_shape(sp.token_version, form, headers.get("x-token-shape"))
        expires_in = resolve_expiry(sp.token_lifetime_seconds, headers)
        roles = apply_roles_override(resolve_roles(sp_key, sp, aud), headers)
        provider = get_provider(sp._provider)
        claims = provider.sp_claims(issuer, sp._canonical_id, sp, aud, shape, expires_in, roles)
        if sp.override_any_claim:
            apply_overrides(claims, form, allow_iss=sp.override_iss_too)
        signing_alg = sp.signing_alg

    elif grant_type == "urn:ietf:params:oauth:grant-type:token-exchange":
        sp_key = form.get("client_id") or ""
        sp = ctx.service_principals.get(sp_key)
        if not sp or sp.secret != form.get("client_secret"):
            raise HTTPException(401, "invalid_client")

        subject_token_str = (form.get("subject_token") or "").strip()
        if not subject_token_str:
            raise HTTPException(
                400,
                detail={"error": "invalid_request", "error_description": "subject_token required"},
            )

        subject_claims = verify_token(subject_token_str, ctx.keys.jwks_keys(issuer))
        if subject_claims is None:
            raise HTTPException(
                400,
                detail={"error": "invalid_request", "error_description": "subject_token signature invalid"},
            )
        if subject_claims.get("exp", 0) < int(time.time()):
            raise HTTPException(
                400,
                detail={"error": "invalid_request", "error_description": "subject_token is expired"},
            )

        aud = form.get("audience") or resolve_aud(form)
        check_audience(sp_key, sp, aud, mode=effective_mode)
        shape = resolve_shape(sp.token_version, form, headers.get("x-token-shape"))
        expires_in = resolve_expiry(sp.token_lifetime_seconds, headers)
        roles = apply_roles_override(resolve_roles(sp_key, sp, aud), headers)
        provider = get_provider(sp._provider)
        claims = provider.sp_claims(issuer, sp._canonical_id, sp, aud, shape, expires_in, roles)

        for _c in ("sub", "oid", "tid", "upn", "preferred_username", "unique_name", "name"):
            if _c in subject_claims:
                claims[_c] = subject_claims[_c]

        claims["act"] = {"sub": sp._canonical_id}
        omit(claims, headers.get("x-omit-claims"))
        access_token = sign(claims, ctx.keys.signing_key_for_alg(issuer, sp.signing_alg))
        await _emit_event("token_issued", issuer, grant_type, claims, ctx)
        return {
            "access_token": access_token,
            "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "token_type": "Bearer",
            "expires_in": max(0, claims["exp"] - int(time.time())),
        }

    else:
        raise HTTPException(400, "unsupported_grant_type")

    omit(claims, headers.get("x-omit-claims"))
    access_token = sign(claims, ctx.keys.signing_key_for_alg(issuer, signing_alg))
    await _emit_event("token_issued", issuer, grant_type, claims, ctx)
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": max(0, claims["exp"] - int(time.time())),
        "scope": form.get("scope", "openid profile email"),
    }


async def handle_token_wrong_sig(issuer: str, request: Any, ctx: TokenContext) -> dict:
    form = dict(await request.form())
    headers = {k.lower(): v for k, v in request.headers.items()}
    aud = resolve_aud(form)
    effective_mode = ctx.issuer_modes.get(issuer) or ctx.mode
    signing_alg = "RS256"

    if form.get("grant_type") == "password":
        user_key = form.get("username") or ""
        user = ctx.users.get(user_key)
        if not user or user.password != form.get("password"):
            raise HTTPException(401, "invalid_grant")
        check_audience(user_key, user, aud, mode=effective_mode)
        shape = resolve_shape(user.token_version, form, headers.get("x-token-shape"))
        roles = resolve_roles(user_key, user, aud)
        provider = get_provider(user._provider)
        claims = provider.user_claims(issuer, user, aud, shape, 3600, roles, form.get("client_id"))
        signing_alg = user.signing_alg
    else:
        sp_key = form.get("client_id") or ""
        sp = ctx.service_principals.get(sp_key)
        if not sp or sp.secret != form.get("client_secret"):
            raise HTTPException(401, "invalid_client")
        check_audience(sp_key, sp, aud, mode=effective_mode)
        shape = resolve_shape(sp.token_version, form, headers.get("x-token-shape"))
        roles = resolve_roles(sp_key, sp, aud)
        provider = get_provider(sp._provider)
        claims = provider.sp_claims(issuer, sp._canonical_id, sp, aud, shape, 3600, roles)
        signing_alg = sp.signing_alg

    return {
        "access_token": sign(claims, ctx.keys.alt_key_for_alg(issuer, signing_alg)),
        "token_type": "Bearer",
        "expires_in": 3600,
    }


async def handle_token_unsigned(issuer: str, request: Any, ctx: TokenContext) -> dict:
    form = dict(await request.form())
    headers = {k.lower(): v for k, v in request.headers.items()}
    aud = resolve_aud(form)
    effective_mode = ctx.issuer_modes.get(issuer) or ctx.mode

    if form.get("grant_type") == "password":
        user_key = form.get("username") or ""
        user = ctx.users.get(user_key)
        if not user or user.password != form.get("password"):
            raise HTTPException(401, "invalid_grant")
        check_audience(user_key, user, aud, mode=effective_mode)
        shape = resolve_shape(user.token_version, form, headers.get("x-token-shape"))
        roles = resolve_roles(user_key, user, aud)
        provider = get_provider(user._provider)
        claims = provider.user_claims(issuer, user, aud, shape, 3600, roles, form.get("client_id"))
    else:
        sp_key = form.get("client_id") or ""
        sp = ctx.service_principals.get(sp_key)
        if not sp or sp.secret != form.get("client_secret"):
            raise HTTPException(401, "invalid_client")
        check_audience(sp_key, sp, aud, mode=effective_mode)
        shape = resolve_shape(sp.token_version, form, headers.get("x-token-shape"))
        roles = resolve_roles(sp_key, sp, aud)
        provider = get_provider(sp._provider)
        claims = provider.sp_claims(issuer, sp._canonical_id, sp, aud, shape, 3600, roles)

    return {"access_token": make_unsigned_token(claims), "token_type": "Bearer", "expires_in": 3600}


async def handle_token_wrong_alg(issuer: str, request: Any, ctx: TokenContext) -> dict:
    form = dict(await request.form())
    headers = {k.lower(): v for k, v in request.headers.items()}
    aud = resolve_aud(form)
    effective_mode = ctx.issuer_modes.get(issuer) or ctx.mode

    if form.get("grant_type") == "password":
        user_key = form.get("username") or ""
        user = ctx.users.get(user_key)
        if not user or user.password != form.get("password"):
            raise HTTPException(401, "invalid_grant")
        check_audience(user_key, user, aud, mode=effective_mode)
        shape = resolve_shape(user.token_version, form, headers.get("x-token-shape"))
        roles = resolve_roles(user_key, user, aud)
        provider = get_provider(user._provider)
        claims = provider.user_claims(issuer, user, aud, shape, 3600, roles, form.get("client_id"))
    else:
        sp_key = form.get("client_id") or ""
        sp = ctx.service_principals.get(sp_key)
        if not sp or sp.secret != form.get("client_secret"):
            raise HTTPException(401, "invalid_client")
        check_audience(sp_key, sp, aud, mode=effective_mode)
        shape = resolve_shape(sp.token_version, form, headers.get("x-token-shape"))
        roles = resolve_roles(sp_key, sp, aud)
        provider = get_provider(sp._provider)
        claims = provider.sp_claims(issuer, sp._canonical_id, sp, aud, shape, 3600, roles)

    return {
        "access_token": make_wrong_alg_token(claims, ctx.keys.signing_public_key_pem(issuer)),
        "token_type": "Bearer",
        "expires_in": 3600,
    }


async def handle_jwks(issuer: str, request: Any, ctx: TokenContext) -> dict:
    headers = {k.lower(): v for k, v in request.headers.items()}
    await apply_test_hooks(headers)
    return {"keys": [k.as_dict(private=False) for k in ctx.keys.jwks_keys(issuer)]}


async def handle_discovery(issuer: str, request: Any, ctx: TokenContext) -> dict:
    headers = {k.lower(): v for k, v in request.headers.items()}
    await apply_test_hooks(headers)
    base = f"{ctx.iss_base}/{issuer}"
    return {
        "issuer": base,
        "token_endpoint": f"{base}/token",
        "jwks_uri": f"{base}/jwks",
        "userinfo_endpoint": f"{base}/userinfo",
        "introspection_endpoint": f"{base}/introspect",
        "response_types_supported": ["token", "id_token"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256", "ES256"],
        "scopes_supported": ["openid", "profile", "email"],
        "grant_types_supported": [
            "client_credentials",
            "password",
            "urn:ietf:params:oauth:grant-type:token-exchange",
        ],
        "introspection_endpoint_auth_methods_supported": ["client_secret_post"],
    }


_INTROSPECT_PASS_THROUGH = {
    "sub", "iss", "aud", "exp", "iat", "nbf", "jti", "scope",
    "azp", "preferred_username", "name", "upn", "roles", "groups",
    "scp", "tid", "appid", "ver",
}


async def handle_introspect(issuer: str, request: Any, ctx: TokenContext) -> dict:
    form = dict(await request.form())

    caller_id = form.get("client_id") or ""
    caller_secret = form.get("client_secret") or ""
    sp = ctx.service_principals.get(caller_id)
    if not sp or sp.secret != caller_secret:
        raise HTTPException(
            401,
            detail={"error": "invalid_client", "error_description": "invalid client credentials"},
        )

    token_str = (form.get("token") or "").strip()
    if not token_str:
        raise HTTPException(
            400,
            detail={"error": "invalid_request", "error_description": "token parameter required"},
        )

    claims = verify_token(token_str, ctx.keys.jwks_keys(issuer))
    if claims is None:
        return {"active": False}
    if claims.get("exp", 0) < int(time.time()):
        return {"active": False}

    response: dict = {"active": True, "token_type": "Bearer"}
    for claim, value in claims.items():
        if claim in _INTROSPECT_PASS_THROUGH:
            response[claim] = value
    if "azp" in claims and "client_id" not in response:
        response["client_id"] = claims["azp"]
    return response


async def handle_userinfo(issuer: str, authorization: str, ctx: TokenContext) -> JSONResponse:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    claims = verify_token(authorization[7:], ctx.keys.jwks_keys(issuer))
    if claims is None:
        raise HTTPException(401, "invalid token")
    return JSONResponse(claims)


# ── Internal ───────────────────────────────────────────────────────────────

async def _emit_event(event_type: str, issuer: str, grant_type: str, claims: dict, ctx: TokenContext) -> None:
    await fire_webhooks(event_type, {"issuer": issuer, "grant_type": grant_type, "claims": claims})
    try:
        await ctx.store.write_token_event({**claims, "grant_type": grant_type})
    except Exception:
        _log.exception("write_token_event failed — token issuance unaffected")
