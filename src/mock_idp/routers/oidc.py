import time

from authlib.jose import jwt
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import config as _cfg
from ..keys import get_alt_key, get_signing_key
from ..tokens import (
    apply_overrides,
    check_audience,
    client_claims,
    omit,
    resolve_aud,
    resolve_expiry,
    resolve_shape,
    sign,
    user_claims,
)

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/{issuer}/.well-known/openid-configuration")
async def discovery(issuer: str):
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
async def jwks(issuer: str):
    return {"keys": [get_signing_key().as_dict(is_private=False)]}


@router.post("/{issuer}/token")
async def token(issuer: str, request: Request):
    form = dict(await request.form())
    headers = {k.lower(): v for k, v in request.headers.items()}
    grant_type = form.get("grant_type")
    aud = resolve_aud(form)

    if grant_type == "password":
        user = _cfg.USERS.get(form.get("username") or "")
        if not user or user.password != form.get("password"):
            raise HTTPException(401, "invalid_grant")
        check_audience(user, aud)
        shape = resolve_shape(user.token_version, form, headers.get("x-token-shape"))
        expires_in = resolve_expiry(user.token_lifetime_seconds, headers)
        claims = user_claims(issuer, user, aud, shape, expires_in, form.get("client_id"))

    elif grant_type == "client_credentials":
        client_key = form.get("client_id") or ""
        client = _cfg.CLIENTS.get(client_key)
        if not client or client.secret != form.get("client_secret"):
            raise HTTPException(401, "invalid_client")
        check_audience(client, aud)
        shape = resolve_shape(client.token_version, form, headers.get("x-token-shape"))
        expires_in = resolve_expiry(client.token_lifetime_seconds, headers)
        claims = client_claims(issuer, client._canonical_id, client, aud, shape, expires_in)
        if client.override_any_claim:
            apply_overrides(claims, form)

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

    if form.get("grant_type") == "password":
        user = _cfg.USERS.get(form.get("username") or "")
        if not user or user.password != form.get("password"):
            raise HTTPException(401, "invalid_grant")
        check_audience(user, aud)
        shape = resolve_shape(user.token_version, form, headers.get("x-token-shape"))
        claims = user_claims(issuer, user, aud, shape, 3600, form.get("client_id"))
    else:
        client_key = form.get("client_id") or ""
        client = _cfg.CLIENTS.get(client_key)
        if not client or client.secret != form.get("client_secret"):
            raise HTTPException(401, "invalid_client")
        check_audience(client, aud)
        shape = resolve_shape(client.token_version, form, headers.get("x-token-shape"))
        claims = client_claims(issuer, client._canonical_id, client, aud, shape, 3600)

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
