"""Entra ID (Azure AD) token claim shape — v1 and v2 formats."""

import time
from typing import Optional

from .. import config as _cfg
from ..models import ServicePrincipalRecord, UserRecord


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
    roles: list[str],
    oauth_client_id: Optional[str],
) -> dict:
    c = _common(issuer, aud, expires_in)
    c["sub"] = user.oid
    c["oid"] = user.oid
    c["tid"] = user.tid
    c["roles"] = roles
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


def sp_claims(
    issuer: str,
    canonical_id: str,
    sp: ServicePrincipalRecord,
    aud: str,
    shape: str,
    expires_in: int,
    roles: list[str],
) -> dict:
    c = _common(issuer, aud, expires_in)
    c["sub"] = canonical_id
    c["tid"] = sp.tid
    c["roles"] = roles
    c["groups"] = list(sp.groups)
    if shape == "v1":
        c["appid"] = canonical_id
        c["ver"] = "1.0"
    else:
        c["azp"] = canonical_id
        c["ver"] = "2.0"
    if sp.extra_claims:
        c.update(sp.extra_claims)
    return c
