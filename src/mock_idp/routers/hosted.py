"""Hosted (Pro) slug router: /{org}/{issuer}/*.

Enabled only when ``MOCK_IDP_HOSTED=true``. Each request:
  1. Resolves the org slug → OrgContext (404 if unknown).
  2. Validates the API key from the Authorization header (sandbox skips this).
  3. Enforces per-org rate limiting for sandbox orgs.
  4. Delegates to the shared token_handler with per-org key / identity context.

Route paths have three segments (/{org}/{issuer}/…) which do not conflict
with the flat router's two-segment paths (/{issuer}/…).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import config as _cfg
from .. import org_registry as _org_registry
from ..org_registry import OrgContext
from ..tokens import (
    apply_overrides,
    apply_roles_override,
    apply_test_hooks,
    check_audience,
    omit,
    resolve_aud,
    resolve_expiry,
    resolve_roles,
    resolve_shape,
    sign,
    verify_token,
)
from ..providers import get_provider
from ..webhooks import fire_webhooks

_log = logging.getLogger(__name__)

router = APIRouter()

_SANDBOX_RATE_WINDOW = 60  # seconds
_SANDBOX_RATE_LIMIT = 10   # requests per window per source IP
_sandbox_hits: dict[str, list[float]] = {}  # ip → list of timestamps


def _get_org(slug: str, authorization: Optional[str]) -> OrgContext:
    ctx = _org_registry.registry.get(slug)
    if ctx is None:
        raise HTTPException(404, detail={"error": "org_not_found"})
    if not ctx.verify_api_key(authorization):
        raise HTTPException(401, detail={"error": "invalid_api_key"})
    return ctx


def _sandbox_rate_check(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    hits = _sandbox_hits.get(ip, [])
    hits = [t for t in hits if now - t < _SANDBOX_RATE_WINDOW]
    if len(hits) >= _SANDBOX_RATE_LIMIT:
        raise HTTPException(
            429,
            detail={"error": "rate_limit_exceeded"},
            headers={"Retry-After": str(_SANDBOX_RATE_WINDOW)},
        )
    hits.append(now)
    _sandbox_hits[ip] = hits


def _full_issuer(org: str, issuer: str) -> str:
    """Combine org slug + issuer slug into the scoped issuer string.

    Providers use this as the ``issuer`` parameter when building claims, so
    the resulting ``iss`` claim becomes ``{ISS_BASE}/{org}/{issuer}`` — a
    stable, per-org URL that matches the hosted JWKS endpoint.
    """
    return f"{org}/{issuer}"


async def _resolve(
    org: str, issuer: str, authorization: Optional[str], request: Request
) -> tuple[OrgContext, str]:
    """Return (OrgContext, full_issuer_string)."""
    org_ctx = _get_org(org, authorization)
    if org_ctx.tier == "sandbox":
        _sandbox_rate_check(request)
    return org_ctx, _full_issuer(org, issuer)


# ── Discovery + JWKS ───────────────────────────────────────────────────────

@router.get("/{org}/{issuer}/.well-known/openid-configuration")
async def hosted_discovery(
    org: str, issuer: str, request: Request,
    authorization: Optional[str] = Header(None),
):
    org_ctx, full_issuer = await _resolve(org, issuer, authorization, request)
    # Build discovery using full_issuer so token_endpoint/jwks_uri include org slug
    headers = {k.lower(): v for k, v in request.headers.items()}
    await apply_test_hooks(headers)
    base = f"{_cfg.ISS_BASE}/{full_issuer}"
    return {
        "issuer": base,
        "token_endpoint": f"{_cfg.ISS_BASE}/{org}/{issuer}/token",
        "jwks_uri": f"{_cfg.ISS_BASE}/{org}/{issuer}/jwks",
        "userinfo_endpoint": f"{_cfg.ISS_BASE}/{org}/{issuer}/userinfo",
        "introspection_endpoint": f"{_cfg.ISS_BASE}/{org}/{issuer}/introspect",
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


@router.get("/{org}/{issuer}/jwks")
async def hosted_jwks(
    org: str, issuer: str, request: Request,
    authorization: Optional[str] = Header(None),
):
    org_ctx, full_issuer = await _resolve(org, issuer, authorization, request)
    headers = {k.lower(): v for k, v in request.headers.items()}
    await apply_test_hooks(headers)
    keys = await org_ctx.key_resolver.async_jwks_keys(full_issuer)
    return {"keys": [k.as_dict(private=False) for k in keys]}


# ── Token issuance ─────────────────────────────────────────────────────────

@router.post("/{org}/{issuer}/token")
async def hosted_token(
    org: str, issuer: str, request: Request,
    authorization: Optional[str] = Header(None),
):
    org_ctx, full_issuer = await _resolve(org, issuer, authorization, request)
    form = dict(await request.form())
    headers = {k.lower(): v for k, v in request.headers.items()}
    await apply_test_hooks(headers)

    grant_type = form.get("grant_type")
    aud = resolve_aud(form)
    store = org_ctx.store
    effective_mode = store.issuer_modes.get(issuer) or store.mode
    signing_alg = "RS256"

    if grant_type == "password":
        user_key = form.get("username") or ""
        user = store.users.get(user_key)
        if not user or user.password != form.get("password"):
            raise HTTPException(401, "invalid_grant")
        check_audience(user_key, user, aud, mode=effective_mode)
        shape = resolve_shape(user.token_version, form, headers.get("x-token-shape"))
        expires_in = resolve_expiry(user.token_lifetime_seconds, headers)
        roles = apply_roles_override(resolve_roles(user_key, user, aud), headers)
        provider = get_provider(user._provider)
        claims = provider.user_claims(full_issuer, user, aud, shape, expires_in, roles, form.get("client_id"))
        signing_alg = user.signing_alg

    elif grant_type == "client_credentials":
        sp_key = form.get("client_id") or ""
        sp = store.service_principals.get(sp_key)
        if not sp or sp.secret != form.get("client_secret"):
            raise HTTPException(401, "invalid_client")
        check_audience(sp_key, sp, aud, mode=effective_mode)
        shape = resolve_shape(sp.token_version, form, headers.get("x-token-shape"))
        expires_in = resolve_expiry(sp.token_lifetime_seconds, headers)
        roles = apply_roles_override(resolve_roles(sp_key, sp, aud), headers)
        provider = get_provider(sp._provider)
        claims = provider.sp_claims(full_issuer, sp._canonical_id, sp, aud, shape, expires_in, roles)
        if sp.override_any_claim:
            apply_overrides(claims, form, allow_iss=sp.override_iss_too)
        signing_alg = sp.signing_alg

    elif grant_type == "urn:ietf:params:oauth:grant-type:token-exchange":
        sp_key = form.get("client_id") or ""
        sp = store.service_principals.get(sp_key)
        if not sp or sp.secret != form.get("client_secret"):
            raise HTTPException(401, "invalid_client")

        subject_token_str = (form.get("subject_token") or "").strip()
        if not subject_token_str:
            raise HTTPException(400, detail={"error": "invalid_request", "error_description": "subject_token required"})

        jwks = await org_ctx.key_resolver.async_jwks_keys(full_issuer)
        subject_claims = verify_token(subject_token_str, jwks)
        if subject_claims is None:
            raise HTTPException(400, detail={"error": "invalid_request", "error_description": "subject_token signature invalid"})

        import time as _t
        if subject_claims.get("exp", 0) < int(_t.time()):
            raise HTTPException(400, detail={"error": "invalid_request", "error_description": "subject_token is expired"})

        aud = form.get("audience") or resolve_aud(form)
        check_audience(sp_key, sp, aud, mode=effective_mode)
        shape = resolve_shape(sp.token_version, form, headers.get("x-token-shape"))
        expires_in = resolve_expiry(sp.token_lifetime_seconds, headers)
        roles = apply_roles_override(resolve_roles(sp_key, sp, aud), headers)
        provider = get_provider(sp._provider)
        claims = provider.sp_claims(full_issuer, sp._canonical_id, sp, aud, shape, expires_in, roles)
        for _c in ("sub", "oid", "tid", "upn", "preferred_username", "unique_name", "name"):
            if _c in subject_claims:
                claims[_c] = subject_claims[_c]
        claims["act"] = {"sub": sp._canonical_id}
        omit(claims, headers.get("x-omit-claims"))
        signing_key = await org_ctx.key_resolver.async_signing_key_for_alg(full_issuer, sp.signing_alg)
        access_token = sign(claims, signing_key)
        await _hosted_emit(org, issuer, grant_type, claims, store)
        return {
            "access_token": access_token,
            "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "token_type": "Bearer",
            "expires_in": max(0, claims["exp"] - int(_t.time())),
        }

    else:
        raise HTTPException(400, "unsupported_grant_type")

    omit(claims, headers.get("x-omit-claims"))
    signing_key = await org_ctx.key_resolver.async_signing_key_for_alg(full_issuer, signing_alg)
    access_token = sign(claims, signing_key)
    await _hosted_emit(org, issuer, grant_type, claims, store)
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": max(0, claims["exp"] - int(time.time())),
        "scope": form.get("scope", "openid profile email"),
    }


# ── Introspect + Userinfo ──────────────────────────────────────────────────

@router.post("/{org}/{issuer}/introspect")
async def hosted_introspect(
    org: str, issuer: str, request: Request,
    authorization: Optional[str] = Header(None),
):
    org_ctx, full_issuer = await _resolve(org, issuer, authorization, request)
    form = dict(await request.form())
    caller_id = form.get("client_id") or ""
    caller_secret = form.get("client_secret") or ""
    sp = org_ctx.store.service_principals.get(caller_id)
    if not sp or sp.secret != caller_secret:
        raise HTTPException(401, detail={"error": "invalid_client", "error_description": "invalid client credentials"})

    token_str = (form.get("token") or "").strip()
    if not token_str:
        raise HTTPException(400, detail={"error": "invalid_request", "error_description": "token parameter required"})

    jwks = await org_ctx.key_resolver.async_jwks_keys(full_issuer)
    claims = verify_token(token_str, jwks)
    if claims is None or claims.get("exp", 0) < int(time.time()):
        return {"active": False}

    _PASS = {"sub", "iss", "aud", "exp", "iat", "nbf", "jti", "scope",
             "azp", "preferred_username", "name", "upn", "roles", "groups",
             "scp", "tid", "appid", "ver"}
    response: dict = {"active": True, "token_type": "Bearer"}
    for claim, value in claims.items():
        if claim in _PASS:
            response[claim] = value
    if "azp" in claims and "client_id" not in response:
        response["client_id"] = claims["azp"]
    return response


@router.get("/{org}/{issuer}/userinfo")
async def hosted_userinfo(
    org: str, issuer: str, request: Request,
    authorization: Optional[str] = Header(None),
):
    org_ctx = _get_org(org, None)  # userinfo uses Bearer token as both auth + payload
    bearer = request.headers.get("authorization", "")
    if not bearer.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    jwks = await org_ctx.key_resolver.async_jwks_keys(_full_issuer(org, issuer))
    claims = verify_token(bearer[7:], jwks)
    if claims is None:
        raise HTTPException(401, "invalid token")
    return JSONResponse(claims)


# ── Test endpoints (available in hosted mode for parity) ──────────────────

@router.post("/{org}/{issuer}/token/wrong-sig")
async def hosted_token_wrong_sig(
    org: str, issuer: str, request: Request,
    authorization: Optional[str] = Header(None),
):
    org_ctx, full_issuer = await _resolve(org, issuer, authorization, request)
    form = dict(await request.form())
    headers = {k.lower(): v for k, v in request.headers.items()}
    aud = resolve_aud(form)
    effective_mode = org_ctx.store.issuer_modes.get(issuer) or org_ctx.store.mode
    signing_alg = "RS256"

    if form.get("grant_type") == "password":
        user_key = form.get("username") or ""
        user = org_ctx.store.users.get(user_key)
        if not user or user.password != form.get("password"):
            raise HTTPException(401, "invalid_grant")
        check_audience(user_key, user, aud, mode=effective_mode)
        shape = resolve_shape(user.token_version, form, headers.get("x-token-shape"))
        roles = resolve_roles(user_key, user, aud)
        provider = get_provider(user._provider)
        claims = provider.user_claims(full_issuer, user, aud, shape, 3600, roles, form.get("client_id"))
        signing_alg = user.signing_alg
    else:
        sp_key = form.get("client_id") or ""
        sp = org_ctx.store.service_principals.get(sp_key)
        if not sp or sp.secret != form.get("client_secret"):
            raise HTTPException(401, "invalid_client")
        check_audience(sp_key, sp, aud, mode=effective_mode)
        shape = resolve_shape(sp.token_version, form, headers.get("x-token-shape"))
        roles = resolve_roles(sp_key, sp, aud)
        provider = get_provider(sp._provider)
        claims = provider.sp_claims(full_issuer, sp._canonical_id, sp, aud, shape, 3600, roles)
        signing_alg = sp.signing_alg

    alt_key = await org_ctx.key_resolver.async_alt_key_for_alg(full_issuer, signing_alg)
    return {"access_token": sign(claims, alt_key), "token_type": "Bearer", "expires_in": 3600}


@router.get("/{org}/{issuer}/token/malformed")
async def hosted_token_malformed(org: str, issuer: str):
    if _org_registry.registry.get(org) is None:
        raise HTTPException(404, detail={"error": "org_not_found"})
    return {
        "access_token": (
            "eyJhbGciOiJSUzI1NiJ9"
            ".this-is-not-base64-or-json"
            ".signature-bytes-garbage"
        )
    }


# ── Internal ───────────────────────────────────────────────────────────────

async def _hosted_emit(org: str, issuer: str, grant_type: str, claims: dict, store) -> None:
    full_issuer = f"{org}/{issuer}"
    await fire_webhooks("token_issued", {"issuer": full_issuer, "grant_type": grant_type, "claims": claims})
    try:
        await store.write_token_event({**claims, "grant_type": grant_type})
    except Exception:
        _log.exception("write_token_event failed — token issuance unaffected")
