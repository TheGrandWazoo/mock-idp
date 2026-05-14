import os
from pathlib import Path

import yaml

from .models import AppConfig, ClientRecord, UserRecord

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
# Env var takes precedence so the token can live in a Kubernetes Secret
# without touching the ConfigMap.
ADMIN_TOKEN: str = os.getenv("MOCK_IDP_ADMIN_TOKEN") or _config.admin_token
CORS_ORIGINS: list[str] = _config.cors_allow_origins
USERS: dict[str, UserRecord] = _config.users

CLIENTS: dict[str, ClientRecord] = {}
for _key, _rec in _config.clients.items():
    _canonical = _rec.client_id or _key
    _rec._canonical_id = _canonical
    CLIENTS[_key] = _rec
    if _canonical != _key:
        CLIENTS[_canonical] = _rec

_clients_raw: dict[str, ClientRecord] = {
    k: v for k, v in CLIENTS.items() if k in _config.clients
}
