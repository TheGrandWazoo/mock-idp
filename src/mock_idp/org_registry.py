"""Per-org state management for hosted (Pro) mode.

The OrgRegistry maps org slugs to OrgContext instances. Each OrgContext owns:
  - a PostgresIdentityStore scoped to that org's org_id
  - a per-org key store whose keys are persisted in Postgres so JWKS URLs
    remain stable across pod restarts

Requires ``MOCK_IDP_HOSTED=true`` and a Postgres backend. The registry is
initialised by main.py during lifespan startup and is a no-op (empty) in
Community/flat mode.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
from dataclasses import dataclass

from joserfc.jwk import ECKey, RSAKey

from .keys import _IssuerKeys  # reuse key class; we manage lifecycle here
from .store.pg_store import PostgresIdentityStore
from .token_handler import TokenContext

_log = logging.getLogger(__name__)

_JWK = RSAKey | ECKey

SANDBOX_ORG_ID = "sandbox"
SANDBOX_RATE_LIMIT = int(os.getenv("MOCK_IDP_SANDBOX_RATE_LIMIT", "10"))  # req/min


class _OrgKeyResolver:
    """KeyResolver backed by per-org, Postgres-persisted _IssuerKeys."""

    def __init__(self, org_id: str, pool) -> None:
        self._org_id = org_id
        self._pool = pool
        self._lock = threading.Lock()
        self._cache: dict[str, _IssuerKeys] = {}  # issuer_slug → keys (in-memory after load)

    async def _load_or_create(self, issuer: str) -> _IssuerKeys:
        """Return cached keys, or load from Postgres, or generate + persist."""
        with self._lock:
            if issuer in self._cache:
                return self._cache[issuer]

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT key_data FROM org_jwks WHERE org_id = $1 AND issuer_slug = $2",
                self._org_id,
                issuer,
            )

        if row:
            ik = _deserialize_issuer_keys(issuer, row["key_data"])
        else:
            ik = _IssuerKeys(f"{self._org_id}/{issuer}")
            await self._persist(issuer, ik)

        with self._lock:
            self._cache[issuer] = ik
        return ik

    async def _persist(self, issuer: str, ik: _IssuerKeys) -> None:
        data = _serialize_issuer_keys(ik)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO org_jwks (org_id, issuer_slug, key_data)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (org_id, issuer_slug)
                DO UPDATE SET key_data = EXCLUDED.key_data, updated_at = now()
                """,
                self._org_id,
                issuer,
                json.dumps(data),
            )

    async def rotate(self, issuer: str) -> str:
        """Rotate signing key, persist, return new kid."""
        ik = await self._load_or_create(issuer)
        ik.rotate()
        await self._persist(issuer, ik)
        return ik.signing.kid

    # ── KeyResolver protocol ───────────────────────────────────────────────

    def signing_key_for_alg(self, issuer: str, alg: str):
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self._load_or_create(issuer)
        ).signing_key_for_alg(alg)

    def alt_key_for_alg(self, issuer: str, alg: str):
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self._load_or_create(issuer)
        ).alt_key_for_alg(alg)

    def jwks_keys(self, issuer: str) -> list[_JWK]:
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self._load_or_create(issuer)
        ).jwks_keys()

    def signing_public_key_pem(self, issuer: str) -> bytes:
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self._load_or_create(issuer)
        ).signing_public_key_pem()

    # Async versions for use in async route handlers (avoids event loop nesting)
    async def async_signing_key_for_alg(self, issuer: str, alg: str):
        return (await self._load_or_create(issuer)).signing_key_for_alg(alg)

    async def async_alt_key_for_alg(self, issuer: str, alg: str):
        return (await self._load_or_create(issuer)).alt_key_for_alg(alg)

    async def async_jwks_keys(self, issuer: str) -> list[_JWK]:
        return (await self._load_or_create(issuer)).jwks_keys()

    async def async_signing_public_key_pem(self, issuer: str) -> bytes:
        return (await self._load_or_create(issuer)).signing_public_key_pem()


@dataclass
class OrgContext:
    org_id: str
    slug: str
    tier: str  # 'sandbox' | 'pro' | 'enterprise'
    api_key_hash: str | None  # None for sandbox
    store: PostgresIdentityStore
    key_resolver: _OrgKeyResolver

    def verify_api_key(self, authorization: str | None) -> bool:
        if self.tier == "sandbox":
            return True
        if not authorization or not authorization.startswith("Bearer "):
            return False
        key = authorization[7:]
        return hashlib.sha256(key.encode()).hexdigest() == self.api_key_hash

    def make_token_context(self, iss_base: str) -> TokenContext:
        return TokenContext(
            iss_base=f"{iss_base}/{self.slug}",
            users=self.store.users,
            service_principals=self.store.service_principals,
            mode=self.store.mode,
            issuer_modes=self.store.issuer_modes,
            store=self.store,
            keys=_AsyncKeyResolverAdapter(self.key_resolver),
        )


class _AsyncKeyResolverAdapter:
    """Wraps _OrgKeyResolver's async methods to satisfy the sync KeyResolver protocol.

    FastAPI route handlers are async, so we use the async variants directly
    inside handlers. The sync protocol methods are only needed by the Protocol
    type checker; they're never called at runtime from async handlers.
    """

    def __init__(self, resolver: _OrgKeyResolver) -> None:
        self._r = resolver

    # Protocol-required sync stubs (type-checker only — never called at runtime)
    def signing_key_for_alg(self, issuer: str, alg: str):  # pragma: no cover
        raise RuntimeError("use async variant")

    def alt_key_for_alg(self, issuer: str, alg: str):  # pragma: no cover
        raise RuntimeError("use async variant")

    def jwks_keys(self, issuer: str):  # pragma: no cover
        raise RuntimeError("use async variant")

    def signing_public_key_pem(self, issuer: str):  # pragma: no cover
        raise RuntimeError("use async variant")

    # Async variants used by hosted router
    async def async_signing_key_for_alg(self, issuer: str, alg: str):
        return await self._r.async_signing_key_for_alg(issuer, alg)

    async def async_alt_key_for_alg(self, issuer: str, alg: str):
        return await self._r.async_alt_key_for_alg(issuer, alg)

    async def async_jwks_keys(self, issuer: str) -> list[_JWK]:
        return await self._r.async_jwks_keys(issuer)

    async def async_signing_public_key_pem(self, issuer: str) -> bytes:
        return await self._r.async_signing_public_key_pem(issuer)


class OrgRegistry:
    """Thread-safe registry of active org contexts.

    Loaded at startup from the orgs + app_config + identity tables. New orgs
    provisioned at runtime via ``register()``; removed via ``deregister()``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._orgs: dict[str, OrgContext] = {}
        self._pool = None

    async def startup(self, dsn: str) -> None:
        import asyncpg
        self._pool = await asyncpg.create_pool(dsn)
        await self._load_all()
        _log.info("OrgRegistry ready: %d org(s) loaded", len(self._orgs))

    async def shutdown(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def _load_all(self) -> None:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT org_id, slug, tier, api_key_hash FROM orgs")
        for row in rows:
            await self._load_org(row["org_id"], row["slug"], row["tier"], row["api_key_hash"])

    async def _load_org(self, org_id: str, slug: str, tier: str, api_key_hash: str | None) -> OrgContext:
        store = PostgresIdentityStore(None)  # pool injected below
        store._pool = self._pool
        store._org_id = org_id
        await store.reload()

        key_resolver = _OrgKeyResolver(org_id, self._pool)
        ctx = OrgContext(
            org_id=org_id,
            slug=slug,
            tier=tier,
            api_key_hash=api_key_hash,
            store=store,
            key_resolver=key_resolver,
        )
        with self._lock:
            self._orgs[slug] = ctx
        return ctx

    def get(self, slug: str) -> OrgContext | None:
        with self._lock:
            return self._orgs.get(slug)

    async def register(self, slug: str, tier: str = "pro") -> tuple[OrgContext, str]:
        """Create a new org. Returns (OrgContext, plaintext_api_key)."""
        plaintext = secrets.token_urlsafe(32)
        api_key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        org_id = slug  # keep org_id == slug for simplicity

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO orgs (org_id, slug, tier, api_key_hash)
                VALUES ($1, $2, $3, $4)
                """,
                org_id, slug, tier, api_key_hash,
            )
            # Provision a minimal app_config row for this org
            await conn.execute(
                """
                INSERT INTO app_config (org_id, auth_mode, admin_token, cors_allow_origins, issuer_modes)
                VALUES ($1, 'lax', $2, '{\"*\"}', '{}')
                ON CONFLICT (org_id) DO NOTHING
                """,
                org_id,
                secrets.token_urlsafe(16),
            )

        ctx = await self._load_org(org_id, slug, tier, api_key_hash)
        return ctx, plaintext

    async def deregister(self, slug: str) -> bool:
        """Delete an org and all its data. Returns False if not found."""
        async with self._pool.acquire() as conn:
            result = await conn.execute("DELETE FROM orgs WHERE slug = $1", slug)
        if result == "DELETE 0":
            return False
        with self._lock:
            self._orgs.pop(slug, None)
        return True

    async def push_config(self, slug: str, config_yaml: str) -> None:
        """Upload a YAML config for an org and hot-reload its store."""
        import yaml
        from .config import _build_store_from_dict

        ctx = self.get(slug)
        if ctx is None:
            raise KeyError(slug)

        data = yaml.safe_load(config_yaml)
        await _build_store_from_dict(data, ctx.store)
        await ctx.store.reload()


# ── Serialisation helpers ──────────────────────────────────────────────────

def _serialize_issuer_keys(ik: _IssuerKeys) -> dict:
    return {
        "seq": ik._seq,
        "signing_rsa": ik.signing.as_dict(private=True),
        "alt_rsa": ik.alt.as_dict(private=True),
        "decoys": [k.as_dict(private=True) for k in ik.decoys],
        "signing_ec": ik.ec_signing.as_dict(private=True),
        "alt_ec": ik.ec_alt.as_dict(private=True),
    }


def _deserialize_issuer_keys(issuer_label: str, data: str | dict) -> _IssuerKeys:
    if isinstance(data, str):
        data = json.loads(data)

    ik = object.__new__(_IssuerKeys)
    ik._issuer = issuer_label
    ik._seq = data["seq"]
    ik.signing = RSAKey.import_key(data["signing_rsa"])
    ik.alt = RSAKey.import_key(data["alt_rsa"])
    ik.decoys = [RSAKey.import_key(d) for d in data["decoys"]]
    ik.ec_signing = ECKey.import_key(data["signing_ec"])
    ik.ec_alt = ECKey.import_key(data["alt_ec"])
    return ik


# Singleton — populated by main.py when MOCK_IDP_HOSTED=true
registry = OrgRegistry()
