"""Runtime configuration — thin wrapper over the active IdentityStore.

Module-level variables are the public API consumed by routers and token helpers.
Dict variables (USERS, SERVICE_PRINCIPALS, CLIENT_APPS, ISSUER_MODES,
_service_principals_raw) are references to the store's internal dicts, which
are updated in-place on reload — callers holding a reference always see live data.

Scalar variables (MODE, ADMIN_TOKEN, CORS_ORIGINS) are re-bound by
reload_config() after each store reload.

Backend selection
-----------------
Set MOCK_IDP_BACKEND to choose the backing store:
  - ``yaml`` (default) — reads from a YAML ConfigMap at CONFIG_PATH
  - ``file``           — alias for yaml
  - ``postgres``       — reads from Postgres; requires MOCK_IDP_PG_DSN

Swapping the backing store
--------------------------
Change the environment variables and the rest of the application is unaffected.
See store/__init__.py for the full interface.
"""

import os
from pathlib import Path

from .models import ClientAppRecord, ServicePrincipalRecord, UserRecord, WebhookConfig
from .store import IdentityStore, create_store

CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "/etc/mock-idp/config.yaml"))
ISS_BASE = os.getenv("ISS_BASE", "http://localhost:8080")

# ── Backend selection ──────────────────────────────────────────────────────
_backend = os.getenv("MOCK_IDP_BACKEND", "yaml").lower()

if _backend == "postgres":
    _dsn = os.getenv("MOCK_IDP_PG_DSN", "")
    store: IdentityStore = create_store("postgres", dsn=_dsn)
else:
    # "yaml" or "file" — YAML ConfigMap backend (default)
    store = create_store("yaml", path=CONFIG_PATH)

# ── Dict references (updated in-place by store.reload()) ──────────────────
USERS: dict[str, UserRecord] = store.users
SERVICE_PRINCIPALS: dict[str, ServicePrincipalRecord] = store.service_principals
CLIENT_APPS: dict[str, ClientAppRecord] = store.client_apps
ISSUER_MODES: dict[str, str] = store.issuer_modes

# Used by the debug router; raw config-key SPs only (no UUID aliases).
_service_principals_raw: dict[str, ServicePrincipalRecord] = store.service_principals_raw

# ── Scalar values (re-bound by reload_config()) ────────────────────────────
MODE: str = store.mode
# Env var takes precedence so the token can live in a Kubernetes Secret.
ADMIN_TOKEN: str = os.getenv("MOCK_IDP_ADMIN_TOKEN") or store.admin_token
CORS_ORIGINS: list[str] = store.cors_origins
WEBHOOKS: list[WebhookConfig] = list(store.webhooks)


async def reload_config() -> None:
    """Reload identity data from the backing store.

    Called by the file-watcher background task in main.py (YAML backend) or
    POST /admin/reload-config (all backends). Dict references stay valid
    (updated in-place by the store). Scalars are re-read and re-bound here.

    Note: CORS origins are captured at startup by FastAPI's CORSMiddleware and
    are not re-applied until the next pod restart. All other values take effect
    immediately after this call returns.
    """
    global MODE, ADMIN_TOKEN, CORS_ORIGINS, WEBHOOKS
    await store.reload()
    MODE = store.mode
    ADMIN_TOKEN = os.getenv("MOCK_IDP_ADMIN_TOKEN") or store.admin_token
    CORS_ORIGINS = store.cors_origins
    WEBHOOKS = list(store.webhooks)
