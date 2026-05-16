"""YAML-file-backed identity store — the default backend."""

from __future__ import annotations

import difflib
import logging
import os
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from ..models import AppConfig, ClientAppRecord, ServicePrincipalRecord, TenantRecord, UserRecord, WebhookConfig

_log = logging.getLogger(__name__)

# Derived from model fields so they stay in sync automatically.
_VALID_APP_KEYS = set(AppConfig.model_fields)
_VALID_TENANT_KEYS = set(TenantRecord.model_fields)


def _is_secret_ref(v: object) -> bool:
    return isinstance(v, dict) and ("from_env" in v or "from_file" in v)


def _resolve_secret(value: object, location: str) -> str:
    """Resolve a plain string, {from_env: VAR}, or {from_file: /path} secret value."""
    if isinstance(value, str):
        return value
    if not _is_secret_ref(value):
        return str(value)
    ref: dict = value  # type: ignore[assignment]
    if "from_env" in ref:
        var = ref["from_env"]
        if var not in os.environ:
            raise ValueError(f"{location}: environment variable {var!r} is not set")
        return os.environ[var]
    path_str = ref["from_file"]
    try:
        return Path(path_str).read_text().strip()
    except OSError as exc:
        raise ValueError(f"{location}: cannot read secret file {path_str!r}: {exc}") from exc


def _resolve_secrets(raw: dict, config_path: Path) -> dict:
    """Walk the raw YAML dict and resolve all secret references before validation."""
    import copy
    raw = copy.deepcopy(raw)

    if "admin_token" in raw:
        raw["admin_token"] = _resolve_secret(
            raw["admin_token"], f"{config_path}:admin_token"
        )

    for tid, tenant in (raw.get("tenants") or {}).items():
        if not isinstance(tenant, dict):
            continue
        for uname, user in (tenant.get("users") or {}).items():
            if isinstance(user, dict) and "password" in user:
                user["password"] = _resolve_secret(
                    user["password"],
                    f"{config_path}:tenants.{tid}.users.{uname}.password",
                )
        for spname, sp in (tenant.get("service_principals") or {}).items():
            if isinstance(sp, dict) and "secret" in sp:
                sp["secret"] = _resolve_secret(
                    sp["secret"],
                    f"{config_path}:tenants.{tid}.service_principals.{spname}.secret",
                )

    return raw


def _lint_raw(raw: dict, path: Path) -> None:
    """Log warnings for structural issues that Pydantic won't catch on its own."""
    for tid, tenant in (raw.get("tenants") or {}).items():
        if not isinstance(tenant, dict):
            continue

        # SP-like entries nested under 'users' (common copy-paste mistake).
        for uname, user in (tenant.get("users") or {}).items():
            if isinstance(user, dict) and "secret" in user and "password" not in user:
                _log.warning(
                    "%s: tenants.%s.users.%s looks like a service principal "
                    "(has 'secret' but no 'password'). "
                    "Service principals belong under 'service_principals:', not 'users:'.",
                    path, tid, uname,
                )

        # Numeric passwords: YAML parses unquoted integers as int, not str.
        for uname, user in (tenant.get("users") or {}).items():
            pw = user.get("password") if isinstance(user, dict) else None
            if pw is not None and not isinstance(pw, (str, type(None))) and not _is_secret_ref(pw):
                _log.warning(
                    '%s: tenants.%s.users.%s.password — YAML parsed %r as %s. '
                    'Quote it to be explicit:  password: "%s"',
                    path, tid, uname, pw, type(pw).__name__, pw,
                )

        # Numeric secrets on service principals.
        for spname, sp in (tenant.get("service_principals") or {}).items():
            sec = sp.get("secret") if isinstance(sp, dict) else None
            if sec is not None and not isinstance(sec, (str, type(None))) and not _is_secret_ref(sec):
                _log.warning(
                    '%s: tenants.%s.service_principals.%s.secret — YAML parsed %r as %s. '
                    'Quote it to be explicit:  secret: "%s"',
                    path, tid, spname, sec, type(sec).__name__, sec,
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


class YamlIdentityStore:
    """YAML-file-backed IdentityStore — the default backend.

    All mutable dicts (users, service_principals, client_apps, issuer_modes,
    service_principals_raw) are updated in-place on reload() so that callers
    holding a reference always see current data without re-fetching the
    reference. Scalar values (mode, admin_token, cors_origins) are re-read by
    config.reload_config() after each reload.

    On initial load a bad config is fatal (sys.exit). On hot-reload a bad
    config is logged and the previous state is preserved.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        # Scalars — replaced on reload; config.py re-reads them.
        self._mode: str = "lax"
        self._admin_token: str = "change-me"
        self._cors_origins: list[str] = ["*"]
        self._webhooks: list[WebhookConfig] = []
        # Dicts — updated in-place so external references stay valid.
        self._issuer_modes: dict[str, str] = {}
        self._users: dict[str, UserRecord] = {}
        self._service_principals: dict[str, ServicePrincipalRecord] = {}
        self._service_principals_raw: dict[str, ServicePrincipalRecord] = {}
        self._client_apps: dict[str, ClientAppRecord] = {}

        raw = self._read_raw()
        try:
            cfg = self._validate(raw)
        except ValidationError as exc:
            print(_format_validation_error(exc, path), file=sys.stderr)
            sys.exit(1)
        except ValueError as exc:
            print(f"\nSecret resolution failed — {exc}", file=sys.stderr)
            sys.exit(1)
        self._apply(cfg)

    # ── IdentityStore Protocol ─────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def issuer_modes(self) -> dict[str, str]:
        return self._issuer_modes

    @property
    def admin_token(self) -> str:
        return self._admin_token

    @property
    def cors_origins(self) -> list[str]:
        return self._cors_origins

    @property
    def webhooks(self) -> list[WebhookConfig]:
        return self._webhooks

    @property
    def users(self) -> dict[str, UserRecord]:
        return self._users

    @property
    def service_principals(self) -> dict[str, ServicePrincipalRecord]:
        return self._service_principals

    @property
    def service_principals_raw(self) -> dict[str, ServicePrincipalRecord]:
        return self._service_principals_raw

    @property
    def client_apps(self) -> dict[str, ClientAppRecord]:
        return self._client_apps

    async def startup(self) -> None:
        pass  # YAML store initialises synchronously in __init__

    async def shutdown(self) -> None:
        pass  # Nothing to release for a file-backed store

    async def reload(self) -> None:
        """Re-read the config file and update all identity tables in-place.

        If the new config fails validation the error is logged and the current
        state is preserved — the server keeps running with the last good config.
        """
        raw = self._read_raw()
        try:
            cfg = self._validate(raw)
        except ValidationError as exc:
            _log.error(
                "Config reload failed — keeping current state:\n%s",
                _format_validation_error(exc, self._path),
            )
            return
        except ValueError as exc:
            _log.error("Config reload failed — secret resolution error: %s", exc)
            return
        self._apply(cfg)
        _log.info(
            "Config reloaded from %s: %d users, %d service principals, %d client apps",
            self._path,
            len(self._users),
            len(self._service_principals_raw),
            len(self._client_apps),
        )

    # ── Internal helpers ───────────────────────────────────────────────────

    def _read_raw(self) -> dict:
        if self._path.exists():
            with self._path.open() as f:
                return yaml.safe_load(f) or {}
        _log.warning("Config file not found at %s — using empty defaults.", self._path)
        return {}

    def _validate(self, raw: dict) -> AppConfig:
        """Run the structural linter, resolve secrets, then validate via Pydantic."""
        if isinstance(raw, dict):
            _lint_raw(raw, self._path)
            raw = _resolve_secrets(raw, self._path)
        return AppConfig.model_validate(raw)

    def _apply(self, cfg: AppConfig) -> None:
        """Apply a validated AppConfig to internal state."""
        self._mode = cfg.auth_mode
        self._admin_token = cfg.admin_token
        self._cors_origins = list(cfg.cors_allow_origins)
        self._webhooks = list(cfg.webhooks)

        self._issuer_modes.clear()
        self._issuer_modes.update(cfg.issuer_modes)

        new_users: dict[str, UserRecord] = {}
        new_sps: dict[str, ServicePrincipalRecord] = {}
        new_sps_raw: dict[str, ServicePrincipalRecord] = {}
        new_apps: dict[str, ClientAppRecord] = {}

        for tid, tenant in cfg.tenants.items():
            tenant_realm_roles = list(tenant.realm_roles)

            for username, user in tenant.users.items():
                user.tid = tid
                user._tenant_realm_roles = tenant_realm_roles
                new_users[username] = user

            for key, sp in tenant.service_principals.items():
                sp.tid = tid
                sp._name = key
                sp._tenant_realm_roles = tenant_realm_roles
                canonical = sp.client_id or key
                sp._canonical_id = canonical
                new_sps[key] = sp
                new_sps_raw[key] = sp
                if canonical != key:
                    new_sps[canonical] = sp

            for aud, app in tenant.clients.items():
                new_apps[aud] = app

        self._users.clear()
        self._users.update(new_users)
        self._service_principals.clear()
        self._service_principals.update(new_sps)
        self._service_principals_raw.clear()
        self._service_principals_raw.update(new_sps_raw)
        self._client_apps.clear()
        self._client_apps.update(new_apps)
