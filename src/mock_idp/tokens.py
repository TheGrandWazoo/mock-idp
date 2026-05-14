import time
from typing import Optional

from authlib.jose import JsonWebKey, jwt
from fastapi import HTTPException

from . import config as _cfg
from .keys import key_kid
from .models import ClientRecord, UserRecord

_LIST_CLAIMS = {"roles", "groups", "amr"}
_RESERVED_FORM_FIELDS = {
    "grant_type",
    "client_id",
    "client_secret",
    "username",
    "password",
    "resource",
    "scope",
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


def check_audience(identity: UserRecord | ClientRecord, aud: str) -> None:
    if _cfg.MODE != "strict":
        return
    if isinstance(identity, ClientRecord) and identity.override_any_claim:
        return
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


def _common(issuer: str, aud: str, expires_in: int) -> dict:
    now = int(time.time())
    return {
        "iss": f"{_cfg.ISS_BASE}/{issuer}",
        "aud": aud,
        "iat": now,
        "nbf": now,
        "exp": now + expires_in,
    }


def user_claims(
    issuer: str,
    user: UserRecord,
    aud: str,
    shape: str,
    expires_in: int,
    oauth_client_id: Optional[str],
) -> dict:
    c = _common(issuer, aud, expires_in)
    c["sub"] = user.oid
    c["oid"] = user.oid
    c["tid"] = user.tid
    c["roles"] = list(user.roles)
    c["groups"] = list(user.groups)
    if shape == "v1":
        c["upn"] = user.upn
        c["unique_name"] = user.upn
        c["ver"] = "1.0"
        if oauth_client_id:
            c["appid"] = oauth_client_id
    else:
        c["preferred_username"] = user.preferred_username
        c["ver"] = "2.0"
        if oauth_client_id:
            c["azp"] = oauth_client_id
    if user.extra_claims:
        c.update(user.extra_claims)
    return c


def client_claims(
    issuer: str,
    canonical_id: str,
    client: ClientRecord,
    aud: str,
    shape: str,
    expires_in: int,
) -> dict:
    c = _common(issuer, aud, expires_in)
    c["sub"] = canonical_id
    c["tid"] = client.tid
    c["roles"] = list(client.roles)
    c["groups"] = list(client.groups)
    if shape == "v1":
        c["appid"] = canonical_id
        c["ver"] = "1.0"
    else:
        c["azp"] = canonical_id
        c["ver"] = "2.0"
    if client.extra_claims:
        c.update(client.extra_claims)
    return c


def apply_overrides(claims: dict, form: dict) -> None:
    for k, v in form.items():
        if not k or k in _RESERVED_FORM_FIELDS:
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
