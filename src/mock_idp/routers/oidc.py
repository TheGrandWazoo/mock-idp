import time

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import config as _cfg
from ..keys import get_alt_key_for_alg, get_jwks_keys, get_signing_key_for_alg, get_signing_public_key_pem
from ..webhooks import fire_webhooks
from ..providers import get_provider
from ..tokens import (
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
    resolve_user_aud,
    sign,
    verify_token,
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


@router.get("/{issuer}/jwks")
async def jwks(issuer: str, request: Request):
    headers = {k.lower(): v for k, v in request.headers.items()}
    await apply_test_hooks(headers)
    return {"keys": [k.as_dict(private=False) for k in get_jwks_keys(issuer)]}


@router.post("/{issuer}/token")
async def token(issuer: str, request: Request):
    form = dict(await request.form())
    headers = {k.lower(): v for k, v in request.headers.items()}
    await apply_test_hooks(headers)
    grant_type = form.get("grant_type")
    aud = resolve_aud(form)
    provider = get_provider("entra_id")
    effective_mode = _cfg.ISSUER_MODES.get(issuer) or _cfg.MODE

    signing_alg = "RS256"

    if grant_type == "password":
        user_key = form.get("username") or ""
        user = _cfg.USERS.get(user_key)
        if not user or user.password != form.get("password"):
            raise HTTPException(401, "invalid_grant")
        check_audience(user_key, user, aud, mode=effective_mode)
        shape = resolve_shape(user.token_version, form, headers.get("x-token-shape"))
        expires_in = resolve_expiry(user.token_lifetime_seconds, headers)
        roles = apply_roles_override(resolve_roles(user_key, user, aud), headers)
        token_aud = resolve_user_aud(aud)  # UUID for user tokens; URI unchanged for SPs
        claims = provider.user_claims(issuer, user, token_aud, shape, expires_in, roles, form.get("client_id"))
        signing_alg = user.signing_alg

    elif grant_type == "client_credentials":
        sp_key = form.get("client_id") or ""
        sp = _cfg.SERVICE_PRINCIPALS.get(sp_key)
        if not sp or sp.secret != form.get("client_secret"):
            raise HTTPException(401, "invalid_client")
        check_audience(sp_key, sp, aud, mode=effective_mode)
        shape = resolve_shape(sp.token_version, form, headers.get("x-token-shape"))
        expires_in = resolve_expiry(sp.token_lifetime_seconds, headers)
        roles = apply_roles_override(resolve_roles(sp_key, sp, aud), headers)
        claims = provider.sp_claims(issuer, sp._canonical_id, sp, aud, shape, expires_in, roles)
        if sp.override_any_claim:
            apply_overrides(claims, form, allow_iss=sp.override_iss_too)
        signing_alg = sp.signing_alg

    elif grant_type == "urn:ietf:params:oauth:grant-type:token-exchange":
        # RFC 8693 — intermediary authenticates, subject identity is preserved.
        sp_key = form.get("client_id") or ""
        sp = _cfg.SERVICE_PRINCIPALS.get(sp_key)
        if not sp or sp.secret != form.get("client_secret"):
            raise HTTPException(401, "invalid_client")

        subject_token_str = (form.get("subject_token") or "").strip()
        if not subject_token_str:
            raise HTTPException(
                400,
                detail={"error": "invalid_request", "error_description": "subject_token required"},
            )

        subject_claims = verify_token(subject_token_str, get_jwks_keys(issuer))
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

        # audience= takes precedence; fall back to resource/scope.
        aud = form.get("audience") or resolve_aud(form)
        check_audience(sp_key, sp, aud, mode=effective_mode)
        shape = resolve_shape(sp.token_version, form, headers.get("x-token-shape"))
        expires_in = resolve_expiry(sp.token_lifetime_seconds, headers)
        roles = apply_roles_override(resolve_roles(sp_key, sp, aud), headers)

        # Base claims for the intermediary SP (handles ver, azp/appid, iss, exp, …)
        claims = provider.sp_claims(issuer, sp._canonical_id, sp, aud, shape, expires_in, roles)

        # Preserve subject identity from the inbound token.
        for _c in ("sub", "oid", "tid", "upn", "preferred_username", "unique_name", "name"):
            if _c in subject_claims:
                claims[_c] = subject_claims[_c]

        # Record the actor chain per RFC 8693 §4.1.
        claims["act"] = {"sub": sp._canonical_id}

        omit(claims, headers.get("x-omit-claims"))
        access_token = sign(claims, get_signing_key_for_alg(issuer, sp.signing_alg))
        await fire_webhooks("token_issued", {
            "issuer": issuer,
            "grant_type": grant_type,
            "claims": claims,
        })
        return {
            "access_token": access_token,
            "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "token_type": "Bearer",
            "expires_in": max(0, claims["exp"] - int(time.time())),
        }

    else:
        raise HTTPException(400, "unsupported_grant_type")

    omit(claims, headers.get("x-omit-claims"))
    access_token = sign(claims, get_signing_key_for_alg(issuer, signing_alg))
    await fire_webhooks("token_issued", {
        "issuer": issuer,
        "grant_type": grant_type,
        "claims": claims,
    })
    return {
        "access_token": access_token,
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
    effective_mode = _cfg.ISSUER_MODES.get(issuer) or _cfg.MODE
    signing_alg = "RS256"

    if form.get("grant_type") == "password":
        user_key = form.get("username") or ""
        user = _cfg.USERS.get(user_key)
        if not user or user.password != form.get("password"):
            raise HTTPException(401, "invalid_grant")
        check_audience(user_key, user, aud, mode=effective_mode)
        shape = resolve_shape(user.token_version, form, headers.get("x-token-shape"))
        roles = resolve_roles(user_key, user, aud)
        claims = provider.user_claims(issuer, user, aud, shape, 3600, roles, form.get("client_id"))
        signing_alg = user.signing_alg
    else:
        sp_key = form.get("client_id") or ""
        sp = _cfg.SERVICE_PRINCIPALS.get(sp_key)
        if not sp or sp.secret != form.get("client_secret"):
            raise HTTPException(401, "invalid_client")
        check_audience(sp_key, sp, aud, mode=effective_mode)
        shape = resolve_shape(sp.token_version, form, headers.get("x-token-shape"))
        roles = resolve_roles(sp_key, sp, aud)
        claims = provider.sp_claims(issuer, sp._canonical_id, sp, aud, shape, 3600, roles)
        signing_alg = sp.signing_alg

    return {
        "access_token": sign(claims, get_alt_key_for_alg(issuer, signing_alg)),
        "token_type": "Bearer",
        "expires_in": 3600,
    }


@router.post("/{issuer}/token/unsigned")
async def token_unsigned(issuer: str, request: Request):
    """Auth enforced; token issued with alg:none and no signature."""
    form = dict(await request.form())
    headers = {k.lower(): v for k, v in request.headers.items()}
    aud = resolve_aud(form)
    provider = get_provider("entra_id")
    effective_mode = _cfg.ISSUER_MODES.get(issuer) or _cfg.MODE

    if form.get("grant_type") == "password":
        user_key = form.get("username") or ""
        user = _cfg.USERS.get(user_key)
        if not user or user.password != form.get("password"):
            raise HTTPException(401, "invalid_grant")
        check_audience(user_key, user, aud, mode=effective_mode)
        shape = resolve_shape(user.token_version, form, headers.get("x-token-shape"))
        roles = resolve_roles(user_key, user, aud)
        claims = provider.user_claims(issuer, user, aud, shape, 3600, roles, form.get("client_id"))
    else:
        sp_key = form.get("client_id") or ""
        sp = _cfg.SERVICE_PRINCIPALS.get(sp_key)
        if not sp or sp.secret != form.get("client_secret"):
            raise HTTPException(401, "invalid_client")
        check_audience(sp_key, sp, aud, mode=effective_mode)
        shape = resolve_shape(sp.token_version, form, headers.get("x-token-shape"))
        roles = resolve_roles(sp_key, sp, aud)
        claims = provider.sp_claims(issuer, sp._canonical_id, sp, aud, shape, 3600, roles)

    return {
        "access_token": make_unsigned_token(claims),
        "token_type": "Bearer",
        "expires_in": 3600,
    }


@router.post("/{issuer}/token/wrong-alg")
async def token_wrong_alg(issuer: str, request: Request):
    """Auth enforced; token HS256-signed using the RSA public key as HMAC secret."""
    form = dict(await request.form())
    headers = {k.lower(): v for k, v in request.headers.items()}
    aud = resolve_aud(form)
    provider = get_provider("entra_id")
    effective_mode = _cfg.ISSUER_MODES.get(issuer) or _cfg.MODE

    if form.get("grant_type") == "password":
        user_key = form.get("username") or ""
        user = _cfg.USERS.get(user_key)
        if not user or user.password != form.get("password"):
            raise HTTPException(401, "invalid_grant")
        check_audience(user_key, user, aud, mode=effective_mode)
        shape = resolve_shape(user.token_version, form, headers.get("x-token-shape"))
        roles = resolve_roles(user_key, user, aud)
        claims = provider.user_claims(issuer, user, aud, shape, 3600, roles, form.get("client_id"))
    else:
        sp_key = form.get("client_id") or ""
        sp = _cfg.SERVICE_PRINCIPALS.get(sp_key)
        if not sp or sp.secret != form.get("client_secret"):
            raise HTTPException(401, "invalid_client")
        check_audience(sp_key, sp, aud, mode=effective_mode)
        shape = resolve_shape(sp.token_version, form, headers.get("x-token-shape"))
        roles = resolve_roles(sp_key, sp, aud)
        claims = provider.sp_claims(issuer, sp._canonical_id, sp, aud, shape, 3600, roles)

    return {
        "access_token": make_wrong_alg_token(claims, get_signing_public_key_pem(issuer)),
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


_INTROSPECT_PASS_THROUGH = {
    "sub", "iss", "aud", "exp", "iat", "nbf", "jti", "scope",
    "azp", "preferred_username", "name", "upn", "roles", "groups",
    "scp", "tid", "appid", "ver",
}


@router.post("/{issuer}/introspect")
async def introspect(issuer: str, request: Request):
    """RFC 7662 token introspection.

    The caller must authenticate with a valid service-principal client_id and
    client_secret.  Returns {"active": true, ...claims} for a valid,
    non-expired token issued by this server, or {"active": false} for anything
    else (expired, bad signature, malformed, unknown issuer).
    """
    form = dict(await request.form())

    # Authenticate the caller — prevents token disclosure to anonymous clients.
    caller_id = form.get("client_id") or ""
    caller_secret = form.get("client_secret") or ""
    sp = _cfg.SERVICE_PRINCIPALS.get(caller_id)
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

    claims = verify_token(token_str, get_jwks_keys(issuer))
    if claims is None:
        return {"active": False}

    if claims.get("exp", 0) < int(time.time()):
        return {"active": False}

    response: dict = {"active": True, "token_type": "Bearer"}
    for claim, value in claims.items():
        if claim in _INTROSPECT_PASS_THROUGH:
            response[claim] = value
    # Map azp → client_id in the RFC 7662 response
    if "azp" in claims and "client_id" not in response:
        response["client_id"] = claims["azp"]
    return response


@router.get("/{issuer}/userinfo")
async def userinfo(issuer: str, authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    claims = verify_token(authorization[7:], get_jwks_keys(issuer))
    if claims is None:
        raise HTTPException(401, "invalid token")
    return JSONResponse(claims)
