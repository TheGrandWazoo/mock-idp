import os
from typing import Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query

from .. import config as _cfg
from ..keys import key_kid, rotate

router = APIRouter(prefix="/admin")

_HOSTED = os.getenv("MOCK_IDP_HOSTED", "").lower() in ("1", "true", "yes")


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


# ── Hosted org management (MOCK_IDP_HOSTED=true only) ─────────────────────

def _require_hosted():
    if not _HOSTED:
        raise HTTPException(404, detail={"error": "hosted mode not enabled"})


@router.post("/orgs")
async def admin_create_org(
    x_admin_token: Optional[str] = Header(None),
    slug: str = Body(...),
    tier: str = Body(default="pro"),
):
    """Provision a new org. Returns the org slug and a one-time API key."""
    if x_admin_token != _cfg.ADMIN_TOKEN:
        raise HTTPException(403, "invalid admin token")
    _require_hosted()

    from ..org_registry import registry
    if registry.get(slug) is not None:
        raise HTTPException(409, detail={"error": "org_exists", "slug": slug})
    if tier not in ("pro", "enterprise"):
        raise HTTPException(400, detail={"error": "invalid_tier", "allowed": ["pro", "enterprise"]})

    _, api_key = await registry.register(slug, tier=tier)
    return {
        "slug": slug,
        "tier": tier,
        "api_key": api_key,
        "token_endpoint": f"{_cfg.ISS_BASE}/{slug}/{{issuer}}/token",
        "jwks_uri": f"{_cfg.ISS_BASE}/{slug}/{{issuer}}/jwks",
        "note": "Store the api_key now — it will not be shown again.",
    }


@router.get("/orgs")
async def admin_list_orgs(x_admin_token: Optional[str] = Header(None)):
    """List all provisioned orgs (slug, tier, org_id)."""
    if x_admin_token != _cfg.ADMIN_TOKEN:
        raise HTTPException(403, "invalid admin token")
    _require_hosted()

    from ..org_registry import registry
    with registry._lock:
        orgs = [
            {"slug": ctx.slug, "tier": ctx.tier, "org_id": ctx.org_id}
            for ctx in registry._orgs.values()
        ]
    return {"orgs": orgs}


@router.delete("/orgs/{slug}")
async def admin_delete_org(
    slug: str,
    x_admin_token: Optional[str] = Header(None),
):
    """Delete an org and all its identity data."""
    if x_admin_token != _cfg.ADMIN_TOKEN:
        raise HTTPException(403, "invalid admin token")
    _require_hosted()
    if slug == "sandbox":
        raise HTTPException(400, detail={"error": "cannot_delete_sandbox"})

    from ..org_registry import registry
    deleted = await registry.deregister(slug)
    if not deleted:
        raise HTTPException(404, detail={"error": "org_not_found", "slug": slug})
    return {"status": "deleted", "slug": slug}


@router.put("/orgs/{slug}/config")
async def admin_push_config(
    slug: str,
    x_admin_token: Optional[str] = Header(None),
    config_yaml: str = Body(..., media_type="text/plain"),
):
    """Upload a YAML identity config for an org and hot-reload it."""
    if x_admin_token != _cfg.ADMIN_TOKEN:
        raise HTTPException(403, "invalid admin token")
    _require_hosted()

    from ..org_registry import registry
    ctx = registry.get(slug)
    if ctx is None:
        raise HTTPException(404, detail={"error": "org_not_found", "slug": slug})
    if slug == "sandbox":
        raise HTTPException(400, detail={"error": "sandbox_config_is_readonly"})

    try:
        await registry.push_config(slug, config_yaml)
    except Exception as exc:
        raise HTTPException(422, detail={"error": "invalid_config", "detail": str(exc)}) from exc

    return {
        "status": "reloaded",
        "slug": slug,
        "users": len(ctx.store.users),
        "service_principals": len(ctx.store.service_principals),
    }
