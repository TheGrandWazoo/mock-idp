import os
from pathlib import Path

import yaml

from .models import AppConfig, ClientAppRecord, ServicePrincipalRecord, UserRecord

CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "/etc/mock-idp/config.yaml"))
ISS_BASE = os.getenv("ISS_BASE", "http://localhost:8080")


def _load_config(path: Path) -> AppConfig:
    raw: dict = {}
    if path.exists():
        with path.open() as f:
            raw = yaml.safe_load(f) or {}
    return AppConfig.model_validate(raw)


_config = _load_config(CONFIG_PATH)
MODE: str = _config.auth_mode
ISSUER_MODES: dict[str, str] = _config.issuer_modes
# Env var takes precedence so the token can live in a Kubernetes Secret
# without touching the ConfigMap.
ADMIN_TOKEN: str = os.getenv("MOCK_IDP_ADMIN_TOKEN") or _config.admin_token
CORS_ORIGINS: list[str] = _config.cors_allow_origins

USERS: dict[str, UserRecord] = {}
SERVICE_PRINCIPALS: dict[str, ServicePrincipalRecord] = {}  # canonical + alias lookup
CLIENT_APPS: dict[str, ClientAppRecord] = {}  # resource apps keyed by audience URI

_raw_sp_keys: set[str] = set()

for _tid, _tenant in _config.tenants.items():
    for _username, _user in _tenant.users.items():
        _user.tid = _tid
        USERS[_username] = _user
    for _key, _sp in _tenant.service_principals.items():
        _sp.tid = _tid
        _sp._name = _key
        _canonical = _sp.client_id or _key
        _sp._canonical_id = _canonical
        SERVICE_PRINCIPALS[_key] = _sp
        _raw_sp_keys.add(_key)
        if _canonical != _key:
            SERVICE_PRINCIPALS[_canonical] = _sp
    for _aud, _app in _tenant.clients.items():
        CLIENT_APPS[_aud] = _app

_service_principals_raw: dict[str, ServicePrincipalRecord] = {
    k: v for k, v in SERVICE_PRINCIPALS.items() if k in _raw_sp_keys
}
