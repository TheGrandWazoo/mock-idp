from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query

from .. import config as _cfg
from ..keys import key_kid, rotate

router = APIRouter(prefix="/admin")


@router.post("/rotate-jwks")
async def admin_rotate(
    issuer: Optional[str] = None,
    x_admin_token: Optional[str] = Header(None),
):
    if x_admin_token != _cfg.ADMIN_TOKEN:
        raise HTTPException(403, "invalid admin token")
    result = rotate(issuer)
    if isinstance(result, dict):
        return {"status": "rotated", "issuers": result}
    return {"status": "rotated", "new_signing_kid": key_kid(result)}


@router.post("/reload-config")
async def admin_reload(x_admin_token: Optional[str] = Header(None)):
    """Reload identity data from the backing store without restarting the pod.

    For the YAML backend this re-reads the config file (same as the file
    watcher). For the Postgres backend this re-queries all identity tables.
    Returns the count of loaded identities for confirmation.
    """
    if x_admin_token != _cfg.ADMIN_TOKEN:
        raise HTTPException(403, "invalid admin token")
    await _cfg.reload_config()
    return {
        "status": "reloaded",
        "users": len(_cfg.USERS),
        "service_principals": len(_cfg.SERVICE_PRINCIPALS),
        "client_apps": len(_cfg.CLIENT_APPS),
    }


@router.get("/audit")
async def admin_audit(
    x_admin_token: Optional[str] = Header(None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int = Query(default=0, ge=0),
):
    """Return a page of token audit events for this org (Postgres only).

    Cursor-based pagination: pass the ``next_cursor`` from a previous response
    as ``cursor`` to fetch the next page.  Returns an empty list for the YAML
    backend.

    Response:
        events      list of token event objects
        next_cursor int or null — pass as cursor to get the next page
    """
    if x_admin_token != _cfg.ADMIN_TOKEN:
        raise HTTPException(403, "invalid admin token")
    events, next_cursor = await _cfg.store.query_audit(limit=limit, cursor=cursor)
    return {"events": events, "next_cursor": next_cursor}
