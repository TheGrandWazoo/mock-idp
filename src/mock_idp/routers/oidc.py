import time

from authlib.jose import jwt
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import config as _cfg
from ..keys import get_alt_key, get_jwks_keys, get_signing_key
from ..providers import get_provider
from ..tokens import (
    apply_overrides,
    apply_test_hooks,
    check_audience,
    omit,
    resolve_aud,
    resolve_expiry,
    resolve_roles,
    resolve_shape,
    sign,
)

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/{issuer}/.well-known/openid-configuration")
async def discovery(issuer: str, request: Request):
    headers = {k.lower(): v for k, v in request.headers.items()}
    await apply_test_hooks(headers)
    base = f"{_cfg.ISS_BASE}/{issuer}"
    return {
        "issuer": base,
        "token_endpoint": f"{base}/token",
        "jwks_uri": f"{base}/jwks",
        "userinfo_endpoint": f"{base}/userinfo",
        "response_types_supported": ["token", "id_token"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "profile", "email"],
        "grant_types_supported": ["client_credentials", "password"],
    }


@router.get("/{issuer}/jwks")
async def jwks(issuer: str, request: Request):
    headers = {k.lower(): v for k, v in request.headers.items()}
    await apply_test_hooks(headers)
    return {"keys": [k.as_dict(is_private=False) for k in get_jwks_keys()]}


@router.post("/{issuer}/token")
async def token(issuer: str, request: Request):
    form = dict(await request.form())
    headers = {k.lower(): v for k, v in request.headers.items()}
    await apply_test_hooks(headers)
    grant_type = form.get("grant_type")
    aud = resolve_aud(form)
    provider = get_provider("entra_id")

    if grant_type == "password":
        user_key = form.get("username") or ""
        user = _cfg.USERS.get(user_key)
        if not user or user.password != form.get("password"):
            raise HTTPException(401, "invalid_grant")
        check_audience(user_key, user, aud)
        shape = resolve_shape(user.token_version, form, headers.get("x-token-shape"))
        expires_in = resolve_expiry(user.token_lifetime_seconds, headers)
        roles = resolve_roles(user_key, user, aud)
        claims = provider.user_claims(issuer, user, aud, shape, expires_in, roles, form.get("client_id"))

    elif grant_type == "client_credentials":
        sp_key = form.get("client_id") or ""
        sp = _cfg.SERVICE_PRINCIPALS.get(sp_key)
        if not sp or sp.secret != form.get("client_secret"):
            raise HTTPException(401, "invalid_client")
        check_audience(sp_key, sp, aud)
        shape = resolve_shape(sp.token_version, form, headers.get("x-token-shape"))
        expires_in = resolve_expiry(sp.token_lifetime_seconds, headers)
        roles = resolve_roles(sp_key, sp, aud)
        claims = provider.sp_claims(issuer, sp._canonical_id, sp, aud, shape, expires_in, roles)
        if sp.override_any_claim:
            apply_overrides(claims, form, allow_iss=sp.override_iss_too)

    else:
        raise HTTPException(400, "unsupported_grant_type")

    omit(claims, headers.get("x-omit-claims"))
    return {
        "access_token": sign(claims, get_signing_key()),
        "token_type": "Bearer",
        "expires_in": max(0, claims["exp"] - int(time.time())),
        "scope": form.get("scope", "openid profile email"),
    }


@router.post("/{issuer}/token/wrong-sig")
async def token_wrong_sig(issuer: str, request: Request):
    """Auth and audience checks enforced; token signed with the unpublished key."""
    form = dict(await request.form())
    headers = {k.lower(): v for k, v in request.headers.items()}
    aud = resolve_aud(form)
    provider = get_provider("entra_id")

    if form.get("grant_type") == "password":
        user_key = form.get("username") or ""
        user = _cfg.USERS.get(user_key)
        if not user or user.password != form.get("password"):
            raise HTTPException(401, "invalid_grant")
        check_audience(user_key, user, aud)
        shape = resolve_shape(user.token_version, form, headers.get("x-token-shape"))
        roles = resolve_roles(user_key, user, aud)
        claims = provider.user_claims(issuer, user, aud, shape, 3600, roles, form.get("client_id"))
    else:
        sp_key = form.get("client_id") or ""
        sp = _cfg.SERVICE_PRINCIPALS.get(sp_key)
        if not sp or sp.secret != form.get("client_secret"):
            raise HTTPException(401, "invalid_client")
        check_audience(sp_key, sp, aud)
        shape = resolve_shape(sp.token_version, form, headers.get("x-token-shape"))
        roles = resolve_roles(sp_key, sp, aud)
        claims = provider.sp_claims(issuer, sp._canonical_id, sp, aud, shape, 3600, roles)

    return {
        "access_token": sign(claims, get_alt_key()),
        "token_type": "Bearer",
        "expires_in": 3600,
    }


@router.get("/{issuer}/token/malformed")
async def token_malformed(issuer: str):
    return {
        "access_token": (
            "eyJhbGciOiJSUzI1NiJ9"
            ".this-is-not-base64-or-json"
            ".signature-bytes-garbage"
        )
    }


@router.get("/{issuer}/userinfo")
async def userinfo(issuer: str, authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        claims = jwt.decode(authorization[7:], get_signing_key())
    except Exception:
        raise HTTPException(401, "invalid token")
    return JSONResponse(dict(claims))
