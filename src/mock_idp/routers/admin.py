from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from .. import config as _cfg
from ..keys import key_kid, rotate

router = APIRouter(prefix="/admin")


@router.post("/rotate-jwks")
async def admin_rotate(x_admin_token: Optional[str] = Header(None)):
    if x_admin_token != _cfg.ADMIN_TOKEN:
        raise HTTPException(403, "invalid admin token")
    new_key = rotate()
    return {"status": "rotated", "new_signing_kid": key_kid(new_key)}


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
