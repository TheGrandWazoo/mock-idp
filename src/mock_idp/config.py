import difflib
import logging
import os
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import AppConfig, ClientAppRecord, ServicePrincipalRecord, TenantRecord, UserRecord

CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "/etc/mock-idp/config.yaml"))
ISS_BASE = os.getenv("ISS_BASE", "http://localhost:8080")

_log = logging.getLogger(__name__)

# Derived from model fields so they stay in sync with the models automatically.
_VALID_APP_KEYS = set(AppConfig.model_fields)
_VALID_TENANT_KEYS = set(TenantRecord.model_fields)


def _lint_raw(raw: dict, path: Path) -> None:
    """Log warnings for structural issues that Pydantic won't catch on its own.

    Runs before model_validate so callers see actionable messages even when
    the YAML is structurally valid but semantically wrong.
    """
    for tid, tenant in (raw.get("tenants") or {}).items():
        if not isinstance(tenant, dict):
            continue

        # Detect SP-like entries nested under 'users' (a common copy-paste mistake).
        for uname, user in (tenant.get("users") or {}).items():
            if isinstance(user, dict) and "secret" in user and "password" not in user:
                _log.warning(
                    "%s: tenants.%s.users.%s looks like a service principal "
                    "(has 'secret' but no 'password'). "
                    "Service principals belong under 'service_principals:', not 'users:'.",
                    path, tid, uname,
                )

        # Numeric passwords: YAML parses unquoted integers as int, not str.
        # The field validator coerces them, but it's better to be explicit in config.
        for uname, user in (tenant.get("users") or {}).items():
            if isinstance(user, dict) and not isinstance(user.get("password"), (str, type(None))):
                val = user["password"]
                _log.warning(
                    '%s: tenants.%s.users.%s.password — YAML parsed %r as %s. '
                    'Quote it to be explicit:  password: "%s"',
                    path, tid, uname, val, type(val).__name__, val,
                )

        # Same for secrets on service principals.
        for spname, sp in (tenant.get("service_principals") or {}).items():
            if isinstance(sp, dict) and not isinstance(sp.get("secret"), (str, type(None))):
                val = sp["secret"]
                _log.warning(
                    '%s: tenants.%s.service_principals.%s.secret — YAML parsed %r as %s. '
                    'Quote it to be explicit:  secret: "%s"',
                    path, tid, spname, val, type(val).__name__, val,
                )


def _format_validation_error(exc: ValidationError, path: Path) -> str:
    """Return a human-readable summary of a Pydantic ValidationError with fix hints."""
    lines = [
        f"\nConfig validation failed ({exc.error_count()} error(s)) — {path}:",
        "",
    ]
    for err in exc.errors(include_url=False):
        loc = err["loc"]
        path_str = " → ".join(str(p) for p in loc) if loc else "(root)"
        typ = err["type"]
        msg = err["msg"]
        input_val = err.get("input")
        hint = ""

        if typ == "string_type":
            field = loc[-1] if loc else "field"
            hint = (
                f"\n      Fix: YAML parsed this as {type(input_val).__name__}. "
                f'Quote the value in your config:  {field}: "{input_val}"'
            )
        elif typ == "extra_forbidden":
            bad_key = str(loc[-1]) if loc else "?"
            # Choose the right candidate set based on depth in the config tree.
            candidates = _VALID_TENANT_KEYS if "tenants" in loc else _VALID_APP_KEYS
            close = difflib.get_close_matches(bad_key, candidates, n=1, cutoff=0.6)
            hint = (
                f'\n      Fix: did you mean "{close[0]}"?'
                if close
                else f"\n      Fix: valid keys at this level are {sorted(candidates)}"
            )
        elif typ == "missing":
            hint = "\n      Fix: add this required field to your config"
        elif typ in ("literal_error", "enum"):
            ctx = err.get("ctx", {})
            if "expected" in ctx:
                hint = f"\n      Fix: allowed values are {ctx['expected']}"

        lines += [f"  [{path_str}]", f"    {msg}{hint}", ""]

    lines.append("  See config.example.yaml for the expected structure.")
    return "\n".join(lines)


def _load_config(path: Path) -> AppConfig:
    raw: dict = {}
    if path.exists():
        with path.open() as f:
            raw = yaml.safe_load(f) or {}
    else:
        _log.warning("Config file not found at %s — starting with empty defaults.", path)

    if isinstance(raw, dict):
        _lint_raw(raw, path)

    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        msg = _format_validation_error(exc, path)
        # Print directly to stderr so it's visible even before logging is configured.
        print(msg, file=sys.stderr)
        sys.exit(1)


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
