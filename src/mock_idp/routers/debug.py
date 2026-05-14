import base64
import json

from authlib.jose import jwt
from fastapi import APIRouter, HTTPException

from .. import config as _cfg
from ..keys import get_signing_key, key_kid
from ..models import DecodeRequest
from ..tokens import redact

router = APIRouter(prefix="/debug")


@router.post("/decode")
async def debug_decode(body: DecodeRequest):
    parts = body.token.split(".")
    if len(parts) != 3:
        raise HTTPException(400, "not a JWT (expected three segments)")

    def _b64decode(seg: str) -> dict:
        seg += "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg))

    try:
        header = _b64decode(parts[0])
        payload = _b64decode(parts[1])
    except Exception as exc:
        raise HTTPException(400, f"decode failed: {exc}")

    sig_ok = False
    try:
        jwt.decode(body.token, get_signing_key())
        sig_ok = True
    except Exception:
        pass

    return {
        "header": header,
        "payload": payload,
        "signature_validated_against_published_key": sig_ok,
    }


@router.get("/identities")
async def debug_identities():
    return {
        "users": {k: redact(v.model_dump()) for k, v in _cfg.USERS.items()},
        "clients": {k: redact(v.model_dump()) for k, v in _cfg._clients_raw.items()},
    }


@router.get("/config")
async def debug_config():
    return {
        "auth_mode": _cfg.MODE,
        "cors_allow_origins": _cfg.CORS_ORIGINS,
        "iss_base": _cfg.ISS_BASE,
        "user_count": len(_cfg.USERS),
        "client_count": len(_cfg._clients_raw),
        "signing_kid": key_kid(get_signing_key()),
        "alt_kid_present": True,
    }
