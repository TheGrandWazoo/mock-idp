import base64
import json

from fastapi import APIRouter, HTTPException

from .. import config as _cfg
from ..keys import all_jwks_keys, all_signing_kids
from ..models import DecodeRequest
from ..tokens import redact, verify_token

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

    sig_ok = verify_token(body.token, all_jwks_keys()) is not None

    return {
        "header": header,
        "payload": payload,
        "signature_validated_against_published_key": sig_ok,
    }


@router.get("/identities")
async def debug_identities():
    return {
        "users": {k: redact(v.model_dump()) for k, v in _cfg.USERS.items()},
        "service_principals": {
            k: redact(v.model_dump()) for k, v in _cfg._service_principals_raw.items()
        },
        "client_apps": {
            k: v.model_dump() for k, v in _cfg.CLIENT_APPS.items()
        },
    }


@router.get("/config")
async def debug_config():
    return {
        "auth_mode": _cfg.MODE,
        "cors_allow_origins": _cfg.CORS_ORIGINS,
        "iss_base": _cfg.ISS_BASE,
        "user_count": len(_cfg.USERS),
        "service_principal_count": len(_cfg._service_principals_raw),
        "client_app_count": len(_cfg.CLIENT_APPS),
        "signing_kids": all_signing_kids(),
        "alt_kid_present": True,
    }
