"""AWS Cognito access token claim shape.

Differences from Entra ID:
- sub is the Cognito User Pool sub (UUID; we use oid)
- token_use is always "access"
- cognito:username is the username
- cognito:groups is the array of groups
- scope is a space-delimited string (not roles)
- client_id is the OAuth app client ID
- auth_time is the epoch of authentication (same as iat here)
- No tid, no oid claim name, no roles array, no ver
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
    now = int(time.time())
    c = _common(issuer, aud, expires_in)
    c["sub"] = user.oid
    c["token_use"] = "access"
    c["auth_time"] = now
    c["cognito:username"] = user.preferred_username or user.upn or "unknown"
    c["cognito:groups"] = list(user.groups)
    c["scope"] = " ".join(roles) if roles else "openid profile email"
    if oauth_client_id:
        c["client_id"] = oauth_client_id
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
    c["token_use"] = "access"
    c["client_id"] = canonical_id
    c["scope"] = " ".join(roles) if roles else "openid"
    if sp.extra_claims:
        c.update(sp.extra_claims)
    return c
