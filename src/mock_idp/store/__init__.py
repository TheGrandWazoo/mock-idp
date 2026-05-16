"""Pluggable identity-store layer.

The IdentityStore protocol defines the interface every backend must implement.
YamlIdentityStore is the default backend — it reads from a YAML ConfigMap.
PostgresIdentityStore (pg_store.py) is the Postgres-backed alternative.

Adding a new backend
--------------------
1. Create a module in this package (e.g. ``sqlite_store.py``).
2. Implement all properties, ``startup()``, ``shutdown()``, and ``reload()``
   from the protocol.
3. Register it in ``create_store()`` below.
4. Add any required dependencies to pyproject.toml under
   ``[project.optional-dependencies]``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..models import ClientAppRecord, ServicePrincipalRecord, UserRecord
from .yaml_store import YamlIdentityStore


@runtime_checkable
class IdentityStore(Protocol):
    """Minimal interface every backend must satisfy.

    Properties that return dicts must return the *same* dict object across
    calls so that callers holding a reference see live data after reload().
    Backends achieve this by updating dicts in-place rather than replacing them.

    Lifecycle: main.py calls startup() at server start and shutdown() at stop.
    reload() is called by the file watcher (YAML) or POST /admin/reload-config
    (all backends). All three are async so Postgres can await pool operations.
    """

    @property
    def mode(self) -> str: ...

    @property
    def issuer_modes(self) -> dict[str, str]: ...

    @property
    def admin_token(self) -> str: ...

    @property
    def cors_origins(self) -> list[str]: ...

    @property
    def users(self) -> dict[str, UserRecord]: ...

    @property
    def service_principals(self) -> dict[str, ServicePrincipalRecord]: ...

    @property
    def service_principals_raw(self) -> dict[str, ServicePrincipalRecord]:
        """Only original config-key SPs (no UUID aliases). Used by the debug router."""
        ...

    @property
    def client_apps(self) -> dict[str, ClientAppRecord]: ...

    async def startup(self) -> None:
        """One-time initialisation (e.g. create connection pool, load initial data).

        Called by main.py lifespan before the server starts accepting requests.
        """
        ...

    async def shutdown(self) -> None:
        """Clean up resources (e.g. close connection pool).

        Called by main.py lifespan on server shutdown.
        """
        ...

    async def reload(self) -> None:
        """Reload identity data from the backing store.

        Must be safe to call at runtime. If the new data is invalid the
        implementation should log the error and preserve the current state.
        """
        ...


def create_store(backend: str = "yaml", **kwargs) -> IdentityStore:
    """Factory — instantiate and return the requested backend.

    ``kwargs`` are forwarded to the backend constructor.
    - ``yaml``     requires ``path: Path``
    - ``postgres`` requires ``dsn: str``
    """
    if backend in ("yaml", "file"):
        path: Path = kwargs.get("path", Path("/etc/mock-idp/config.yaml"))
        return YamlIdentityStore(path)
    if backend == "postgres":
        try:
            from .pg_store import PostgresIdentityStore  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "Postgres backend requires extra dependencies. "
                "Install them with:  uv sync --extra postgres"
            ) from exc
        dsn: str = kwargs.get("dsn", "")
        if not dsn:
            raise ValueError(
                "MOCK_IDP_PG_DSN must be set when using the postgres backend."
            )
        return PostgresIdentityStore(dsn)
    raise ValueError(
        f"Unknown store backend {backend!r}. "
        "Supported backends: 'yaml', 'postgres'. "
        "See docs/mock-oidc/ADR-003-store-abstraction.md for adding a new backend."
    )


__all__ = ["IdentityStore", "YamlIdentityStore", "create_store"]
