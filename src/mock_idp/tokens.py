from typing import Optional

from authlib.jose import JsonWebKey, jwt
from fastapi import HTTPException

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

    Uses the client-app grants table when a matching ClientAppRecord exists;
    falls back to the flat roles list on the identity otherwise.
    """
    app = _cfg.CLIENT_APPS.get(aud)
    if app is not None:
        # SPs look up grants by their original config name, not by UUID alias
        grants_key = (
            identity._name
            if isinstance(identity, ServicePrincipalRecord) and identity._name
            else identity_key
        )
        return list(app.grants.get(grants_key, []))
    return list(identity.roles)


def check_audience(identity_key: str, identity: UserRecord | ServicePrincipalRecord, aud: str) -> None:
    if _cfg.MODE != "strict":
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


def redact(d: object) -> object:
    if isinstance(d, dict):
        return {
            k: ("***" if k in {"password", "secret", "admin_token"} else redact(v))
            for k, v in d.items()
        }
    if isinstance(d, list):
        return [redact(x) for x in d]
    return d


def sign(claims: dict, key: JsonWebKey) -> str:
    header = {"alg": "RS256", "typ": "JWT", "kid": key_kid(key)}
    return jwt.encode(header, claims, key).decode("utf-8")
