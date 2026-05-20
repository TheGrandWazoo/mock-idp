import logging

from fastapi import APIRouter, Header, Request

from .. import config as _cfg
from .. import ratelimit as _rl
from ..keys import (
    get_alt_key_for_alg,
    get_jwks_keys,
    get_signing_key_for_alg,
    get_signing_public_key_pem,
)
from ..token_handler import TokenContext, handle_discovery, handle_introspect
from ..token_handler import handle_jwks as _handle_jwks
from ..token_handler import (
    handle_token,
    handle_token_unsigned,
    handle_token_wrong_alg,
    handle_token_wrong_sig,
    handle_userinfo,
)

_log = logging.getLogger(__name__)

router = APIRouter()


class _GlobalKeyResolver:
    """Delegates to the module-level key store in keys.py."""

    def signing_key_for_alg(self, issuer: str, alg: str):
        return get_signing_key_for_alg(issuer, alg)

    def alt_key_for_alg(self, issuer: str, alg: str):
        return get_alt_key_for_alg(issuer, alg)

    def jwks_keys(self, issuer: str):
        return get_jwks_keys(issuer)

    def signing_public_key_pem(self, issuer: str) -> bytes:
        return get_signing_public_key_pem(issuer)


_global_keys = _GlobalKeyResolver()


def _ctx() -> TokenContext:
    return TokenContext(
        iss_base=_cfg.ISS_BASE,
        users=_cfg.USERS,
        service_principals=_cfg.SERVICE_PRINCIPALS,
        mode=_cfg.MODE,
        issuer_modes=_cfg.ISSUER_MODES,
        store=_cfg.store,
        keys=_global_keys,
    )


@router.get("/healthz")
async def healthz():
    response: dict = {"status": "ok"}
    if _rl.ENABLED:
        response["rate_limit"] = {"enabled": True, "limit": _rl.RATE_LIMIT}
    return response


@router.get("/{issuer}/.well-known/openid-configuration")
async def discovery(issuer: str, request: Request):
    return await handle_discovery(issuer, request, _ctx())


@router.get("/{issuer}/jwks")
async def jwks(issuer: str, request: Request):
    return await _handle_jwks(issuer, request, _ctx())


@router.post("/{issuer}/token")
@_rl.limit
async def token(issuer: str, request: Request):
    return await handle_token(issuer, request, _ctx())


@router.post("/{issuer}/token/wrong-sig")
async def token_wrong_sig(issuer: str, request: Request):
    return await handle_token_wrong_sig(issuer, request, _ctx())


@router.post("/{issuer}/token/unsigned")
async def token_unsigned(issuer: str, request: Request):
    return await handle_token_unsigned(issuer, request, _ctx())


@router.post("/{issuer}/token/wrong-alg")
async def token_wrong_alg(issuer: str, request: Request):
    return await handle_token_wrong_alg(issuer, request, _ctx())


@router.get("/{issuer}/token/malformed")
async def token_malformed(issuer: str):
    return {
        "access_token": (
            "eyJhbGciOiJSUzI1NiJ9"
            ".this-is-not-base64-or-json"
            ".signature-bytes-garbage"
        )
    }


@router.post("/{issuer}/introspect")
async def introspect(issuer: str, request: Request):
    return await handle_introspect(issuer, request, _ctx())


@router.get("/{issuer}/userinfo")
async def userinfo(issuer: str, authorization: str = Header(...)):
    return await handle_userinfo(issuer, authorization, _ctx())
