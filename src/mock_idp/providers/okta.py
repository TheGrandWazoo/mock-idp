"""Okta token claim shape.

Differences from Entra ID:
- sub is the email / upn (not a UUID)
- uid is the Okta user UID (we use oid)
- cid is the OAuth client_id (equivalent to azp/appid)
- scp is a space-delimited string of scopes/roles (not an array)
- groups is an array of group names
- No tid, no oid as a claim, no ver claim
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
    c["sub"] = user.upn or user.preferred_username or user.oid
    c["uid"] = user.oid
    c["groups"] = list(user.groups)
    c["scp"] = " ".join(roles) if roles else "openid profile email"
    if oauth_client_id:
        c["cid"] = oauth_client_id
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
    c["cid"] = canonical_id
    c["scp"] = " ".join(roles) if roles else "openid"
    c["groups"] = list(sp.groups)
    if sp.extra_claims:
        c.update(sp.extra_claims)
    return c
