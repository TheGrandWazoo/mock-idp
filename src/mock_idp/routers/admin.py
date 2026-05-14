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
