"""Postgres-backed identity store using asyncpg.

Install extras before use:
    uv sync --extra postgres

Then set environment variables:
    MOCK_IDP_BACKEND=postgres
    MOCK_IDP_PG_DSN=postgresql://user:pass@host/mock_idp

Run migrations before first use:
    uv run alembic upgrade head

The store keeps an in-memory cache of all identity data (same in-place dict
update pattern as YamlIdentityStore) so reads are O(1) hash lookups. The
cache is refreshed on startup() and on every reload() call.
"""

from __future__ import annotations

import json
import logging
import os

from ..models import ClientAppRecord, ServicePrincipalRecord, UserRecord

_log = logging.getLogger(__name__)


class PostgresIdentityStore:
    """Postgres-backed IdentityStore.

    All mutable dicts are updated in-place on reload() so that callers
    holding a reference always see current data without re-fetching.
    Scalar values are re-read by config.reload_config() after each reload().
    """

    def __init__(self, dsn: str) -> None:
        # asyncpg is an optional dep; import lazily so the module is safe to
        # import even without the extras installed.
        try:
            import asyncpg  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "asyncpg is required for the Postgres backend. "
                "Install it with:  uv sync --extra postgres"
            ) from exc

        self._dsn = dsn
        self._pool = None  # created in startup()
        self._org_id: str = os.environ.get("MOCK_IDP_ORG_ID", "default")

        # Scalars — replaced on reload; config.py re-reads them.
        self._mode: str = "lax"
        self._admin_token: str = "change-me"
        self._cors_origins: list[str] = ["*"]

        # Dicts — updated in-place so external references stay valid.
        self._issuer_modes: dict[str, str] = {}
        self._users: dict[str, UserRecord] = {}
        self._service_principals: dict[str, ServicePrincipalRecord] = {}
        self._service_principals_raw: dict[str, ServicePrincipalRecord] = {}
        self._client_apps: dict[str, ClientAppRecord] = {}

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

    @property
    def webhooks(self) -> list:
        # Webhooks are static YAML config; not stored in Postgres.
        return []

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def startup(self) -> None:
        """Create the connection pool and load the initial identity cache."""
        import asyncpg

        _log.info("Postgres backend: connecting to %s", _redact_dsn(self._dsn))
        self._pool = await asyncpg.create_pool(self._dsn)
        await self.reload()
        _log.info(
            "Postgres backend ready: %d users, %d service principals, %d client apps",
            len(self._users),
            len(self._service_principals_raw),
            len(self._client_apps),
        )

    async def shutdown(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            _log.info("Postgres backend: connection pool closed")

    async def reload(self) -> None:
        """Re-query Postgres and update all identity tables in-place.

        Safe to call at runtime. Query failures are logged and the current
        in-memory state is preserved.
        """
        if self._pool is None:
            _log.error("Postgres reload called before startup() — skipping")
            return
        try:
            await self._fetch_and_apply()
        except Exception:
            _log.exception("Postgres reload failed — keeping current state")

    async def write_token_event(self, event: dict) -> None:
        """Write one row to token_events. Never raises — failures are logged."""
        if self._pool is None:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO token_events
                        (org_id, issuer, grant_type, sub, aud, iat, exp, jti, raw_claims)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                    """,
                    self._org_id,
                    event.get("iss", ""),
                    event.get("grant_type", ""),
                    event.get("sub", ""),
                    event.get("aud"),
                    int(event.get("iat", 0)),
                    int(event.get("exp", 0)),
                    event.get("jti"),
                    json.dumps(event),
                )
        except Exception:
            _log.exception("write_token_event failed — token issuance unaffected")

    async def query_audit(self, limit: int, cursor: int) -> tuple[list[dict], int | None]:
        """Return up to *limit* token_events with id > cursor for this org."""
        if self._pool is None:
            return [], None
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, org_id, issuer, grant_type, sub, aud,
                           iat, exp, jti, raw_claims, created_at
                    FROM token_events
                    WHERE org_id = $1 AND id > $2
                    ORDER BY id ASC
                    LIMIT $3
                    """,
                    self._org_id,
                    cursor,
                    limit + 1,
                )
        except Exception:
            _log.exception("query_audit failed")
            return [], None

        has_more = len(rows) > limit
        page = rows[:limit]
        events = [
            {
                "id": r["id"],
                "org_id": r["org_id"],
                "issuer": r["issuer"],
                "grant_type": r["grant_type"],
                "sub": r["sub"],
                "aud": r["aud"],
                "iat": r["iat"],
                "exp": r["exp"],
                "jti": r["jti"],
                "raw_claims": _parse_jsonb(r["raw_claims"]),
                "created_at": r["created_at"].isoformat(),
            }
            for r in page
        ]
        next_cursor = page[-1]["id"] if has_more else None
        return events, next_cursor

    # ── Internal helpers ───────────────────────────────────────────────────

    async def _fetch_and_apply(self) -> None:
        async with self._pool.acquire() as conn:
            cfg_row = await conn.fetchrow(
                "SELECT * FROM app_config WHERE org_id = $1", self._org_id
            )
            user_rows = await conn.fetch(
                """
                SELECT u.*, t.provider
                FROM users u JOIN tenants t USING (org_id, tid)
                WHERE u.org_id = $1
                """,
                self._org_id,
            )
            sp_rows = await conn.fetch(
                """
                SELECT s.*, t.provider
                FROM service_principals s JOIN tenants t USING (org_id, tid)
                WHERE s.org_id = $1
                """,
                self._org_id,
            )
            app_rows = await conn.fetch(
                "SELECT * FROM client_apps WHERE org_id = $1", self._org_id
            )

        # Scalars
        if cfg_row:
            self._mode = cfg_row["auth_mode"]
            self._admin_token = cfg_row["admin_token"]
            self._cors_origins = list(cfg_row["cors_allow_origins"] or ["*"])
            raw_issuer_modes = cfg_row["issuer_modes"]
            new_issuer_modes: dict[str, str] = (
                json.loads(raw_issuer_modes)
                if isinstance(raw_issuer_modes, str)
                else dict(raw_issuer_modes or {})
            )
            self._issuer_modes.clear()
            self._issuer_modes.update(new_issuer_modes)

        # Users
        new_users: dict[str, UserRecord] = {}
        for row in user_rows:
            extra = _parse_jsonb(row["extra_claims"])
            user = UserRecord(
                password=row["password"],
                upn=row["upn"],
                preferred_username=row["preferred_username"],
                oid=row["oid"],
                tid=row["tid"],
                token_version=row["token_version"],
                token_lifetime_seconds=row["token_lifetime_seconds"],
                roles=list(row["roles"] or []),
                groups=list(row["groups"] or []),
                allowed_audiences=list(row["allowed_audiences"] or []),
                extra_claims=extra,
            )
            user._provider = row["provider"]
            new_users[row["username"]] = user

        # Service principals
        new_sps: dict[str, ServicePrincipalRecord] = {}
        new_sps_raw: dict[str, ServicePrincipalRecord] = {}
        for row in sp_rows:
            extra = _parse_jsonb(row["extra_claims"])
            sp = ServicePrincipalRecord(
                client_id=row["client_id"],
                secret=row["secret"],
                label=row["label"],
                token_version=row["token_version"],
                token_lifetime_seconds=row["token_lifetime_seconds"],
                roles=list(row["roles"] or []),
                groups=list(row["groups"] or []),
                tid=row["tid"],
                allowed_audiences=list(row["allowed_audiences"] or []),
                extra_claims=extra,
                override_any_claim=row["override_any_claim"],
                override_iss_too=row["override_iss_too"],
            )
            name = row["name"]
            sp._name = name
            sp._provider = row["provider"]
            canonical = sp.client_id or name
            sp._canonical_id = canonical
            new_sps[name] = sp
            new_sps_raw[name] = sp
            if canonical != name:
                new_sps[canonical] = sp

        # Client apps
        new_apps: dict[str, ClientAppRecord] = {}
        for row in app_rows:
            grants = _parse_jsonb(row["grants"])
            app = ClientAppRecord(
                app_id=row["app_id"],
                label=row["label"],
                roles=list(row["roles"] or []),
                grants=grants,
            )
            new_apps[row["audience"]] = app

        # Apply in-place
        self._users.clear()
        self._users.update(new_users)
        self._service_principals.clear()
        self._service_principals.update(new_sps)
        self._service_principals_raw.clear()
        self._service_principals_raw.update(new_sps_raw)
        self._client_apps.clear()
        self._client_apps.update(new_apps)

        _log.info(
            "Postgres reload complete: %d users, %d service principals, %d client apps",
            len(self._users),
            len(self._service_principals_raw),
            len(self._client_apps),
        )


def _parse_jsonb(value: object) -> dict:
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def _redact_dsn(dsn: str) -> str:
    """Replace the password in a DSN with *** for safe logging."""
    try:
        from urllib.parse import urlparse, urlunparse

        parts = urlparse(dsn)
        if parts.password:
            netloc = f"{parts.username}:***@{parts.hostname}"
            if parts.port:
                netloc += f":{parts.port}"
            parts = parts._replace(netloc=netloc)
        return urlunparse(parts)
    except Exception:
        return "<dsn redacted>"
