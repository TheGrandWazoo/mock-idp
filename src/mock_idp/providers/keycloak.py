"""Keycloak access token claim shape.

Differences from Entra ID:
- sub is the Keycloak user UUID (we use oid)
- preferred_username and email are top-level claims
- realm_access.roles holds realm-level roles
- resource_access.<client_id>.roles holds client-scoped roles
- azp is the client_id
- sid is a session ID (derived from oid for determinism)
- scope is a space-delimited string
- No tid, no appid/cid, no ver, no groups top-level claim
- No v1/v2 distinction — shape parameter is ignored
"""

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
    c["preferred_username"] = user.preferred_username or user.upn or "unknown"
    c["email"] = user.upn or user.preferred_username or ""
    c["realm_access"] = {"roles": roles}
    c["scope"] = "openid profile email"
    if oauth_client_id:
        c["azp"] = oauth_client_id
        c["resource_access"] = {oauth_client_id: {"roles": roles}}
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
    c["azp"] = canonical_id
    c["realm_access"] = {"roles": roles}
    c["resource_access"] = {canonical_id: {"roles": roles}}
    c["scope"] = "openid"
    if sp.extra_claims:
        c.update(sp.extra_claims)
    return c
