import asyncio
import base64
import hashlib
import hmac as _hmac
import json as _json
from typing import Optional

from fastapi import HTTPException
from joserfc import jwt as _jwt
from joserfc.jwk import ECKey, KeySet, RSAKey

from . import config as _cfg
from .keys import key_kid
from .models import ServicePrincipalRecord, UserRecord

_LIST_CLAIMS = {"roles", "groups", "amr"}
_RESERVED_FORM_FIELDS = {
    "grant_type",
    "client_id",
    "client_secret",
    "username",
    "password",
    "resource",
    "scope",
    "iss",  # gated separately via override_iss_too
}


def resolve_shape(default: str, form: dict, header_shape: Optional[str]) -> str:
    if header_shape in {"v1", "v2"}:
        return header_shape
    cid = form.get("client_id") or ""
    if cid.endswith("-v1"):
        return "v1"
    if cid.endswith("-v2"):
        return "v2"
    return default or "v2"


def resolve_aud(form: dict) -> str:
    if res := form.get("resource"):
        return res
    scope = form.get("scope") or ""
    if scope:
        return scope[: -len("/.default")] if scope.endswith("/.default") else scope
    return "api://default"


def resolve_user_aud(aud: str) -> str:
    """Return the app_id UUID for user tokens when the client app defines one.

    Entra ID sets aud to the bare UUID (app_id) for user tokens and to the
    Application ID URI (api://...) for service-principal tokens. If no
    ClientAppRecord exists for this audience, or it has no app_id, return
    the URI unchanged so behaviour stays backward-compatible.
    """
    app = _cfg.CLIENT_APPS.get(aud)
    if app and app.app_id:
        return app.app_id
    return aud


def resolve_expiry(default: int, headers: dict) -> int:
    if headers.get("x-test-expired"):
        return -60
    if (override := headers.get("x-test-expires-in")) is not None:
        try:
            return int(override)
        except ValueError:
            pass
    return default or 3600


def resolve_roles(identity_key: str, identity: UserRecord | ServicePrincipalRecord, aud: str) -> list[str]:
    """Resolve roles for the requested audience.

    Merges three layers in order (duplicates removed, first occurrence wins):
      1. Tenant realm_roles   — injected at load time onto identity._tenant_realm_roles
      2. Identity realm_roles — identity.realm_roles (always included, any audience)
      3. Audience roles       — grants table if a ClientAppRecord exists, else identity.roles
    """
    app = _cfg.CLIENT_APPS.get(aud)
    if app is not None:
        grants_key = (
            identity._name
            if isinstance(identity, ServicePrincipalRecord) and identity._name
            else identity_key
        )
        audience_roles = list(app.grants.get(grants_key, []))
    else:
        audience_roles = list(identity.roles)

    seen: set[str] = set()
    result: list[str] = []
    for r in (identity._tenant_realm_roles + list(identity.realm_roles) + audience_roles):
        if r not in seen:
            seen.add(r)
            result.append(r)
    return result


def check_audience(
    identity_key: str,
    identity: UserRecord | ServicePrincipalRecord,
    aud: str,
    mode: Optional[str] = None,
) -> None:
    effective_mode = mode if mode in ("lax", "strict") else _cfg.MODE
    if effective_mode != "strict":
        return
    if isinstance(identity, ServicePrincipalRecord) and identity.override_any_claim:
        return
    app = _cfg.CLIENT_APPS.get(aud)
    if app is not None:
        # grants model: reject if this identity has no grant on the app
        grants_key = (
            identity._name
            if isinstance(identity, ServicePrincipalRecord) and identity._name
            else identity_key
        )
        if grants_key not in app.grants:
            raise HTTPException(
                400,
                detail={
                    "error": "invalid_target",
                    "error_description": (
                        f"Identity {identity_key!r} has no grant on {aud!r}."
                    ),
                },
            )
        return
    # flat model fallback: check allowed_audiences
    if aud not in identity.allowed_audiences:
        raise HTTPException(
            400,
            detail={
                "error": "invalid_target",
                "error_description": (
                    f"Audience {aud!r} is not in allowed_audiences for this identity."
                ),
            },
        )


def apply_overrides(claims: dict, form: dict, allow_iss: bool = False) -> None:
    for k, v in form.items():
        if not k:
            continue
        if k == "iss":
            if allow_iss:
                claims["iss"] = v
            continue
        if k in _RESERVED_FORM_FIELDS:
            continue
        if k in _LIST_CLAIMS and isinstance(v, str):
            claims[k] = [item.strip() for item in v.split(",") if item.strip()]
        elif k in {"exp", "iat", "nbf"}:
            try:
                claims[k] = int(v)
            except (ValueError, TypeError):
                claims[k] = v
        else:
            claims[k] = v


def omit(claims: dict, header_value: Optional[str]) -> None:
    for name in (header_value or "").split(","):
        if name := name.strip():
            claims.pop(name, None)


def apply_roles_override(roles: list[str], headers: dict) -> list[str]:
    """Apply X-Override-Roles test header if present; otherwise return roles unchanged."""
    if (override := headers.get("x-override-roles")) is None:
        return roles
    return [r.strip() for r in override.split(",") if r.strip()]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_unsigned_token(claims: dict) -> str:
    """JWT with alg:none and an empty signature — validators must reject this."""
    header = _b64url(_json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = _b64url(_json.dumps(claims).encode())
    return f"{header}.{payload}."


def make_wrong_alg_token(claims: dict, public_key_pem: bytes) -> str:
    """HS256-signed JWT using the RSA public key PEM as the HMAC secret.

    Classic algorithm-confusion attack: a validator that trusts the alg header
    and accepts HS256 will verify this successfully using the published public key.
    """
    header = _b64url(_json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(_json.dumps(claims).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = _hmac.new(public_key_pem, signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(sig)}"


async def apply_test_hooks(headers: dict) -> None:
    """Honor X-Test-Delay-Ms and X-Test-Fail request headers."""
    delay_ms = headers.get("x-test-delay-ms")
    if delay_ms:
        try:
            await asyncio.sleep(max(0, int(delay_ms)) / 1000)
        except (ValueError, TypeError):
            pass
    if headers.get("x-test-fail"):
        raise HTTPException(500, {"error": "server_error", "error_description": "X-Test-Fail triggered"})


def redact(d: object) -> object:
    if isinstance(d, dict):
        return {
            k: ("***" if k in {"password", "secret", "admin_token"} else redact(v))
            for k, v in d.items()
        }
    if isinstance(d, list):
        return [redact(x) for x in d]
    return d


def sign(claims: dict, key: RSAKey | ECKey) -> str:
    alg = "ES256" if isinstance(key, ECKey) else "RS256"
    header = {"alg": alg, "typ": "JWT", "kid": key_kid(key)}
    return _jwt.encode(header, claims, key)


def verify_token(token_str: str, keys: list[RSAKey | ECKey]) -> dict | None:
    """Verify a JWT against a set of public keys, returning claims or None on failure."""
    if not token_str:
        return None
    try:
        return _jwt.decode(token_str, KeySet(keys)).claims
    except Exception:
        return None
