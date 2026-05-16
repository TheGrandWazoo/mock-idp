"""Pluggable identity-store layer.

The IdentityStore protocol defines the interface every backend must implement.
YamlIdentityStore is the default (and currently only) backend — it reads from
a YAML ConfigMap. Future backends (e.g. PostgresIdentityStore) drop in here
without touching routers, token logic, or config.py.

Adding a new backend
--------------------
1. Create a module in this package (e.g. ``pg_store.py``).
2. Implement all properties and ``reload()`` from the protocol.
3. Register it in ``create_store()`` below.
4. Add any required dependencies to pyproject.toml.
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
    calls so that callers holding a reference see live data after a reload().
    Backends achieve this by updating dicts in-place rather than replacing them.
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
        """Only original config-key SPs (no UUID aliases).  Used by the debug router."""
        ...

    @property
    def client_apps(self) -> dict[str, ClientAppRecord]: ...

    def reload(self) -> None:
        """Reload identity data from the backing store.

        Must be safe to call at runtime (no sys.exit). If the new data is
        invalid the implementation should log the error and preserve the
        current state.
        """
        ...


def create_store(backend: str = "yaml", **kwargs) -> IdentityStore:
    """Factory — instantiate and return the requested backend.

    Currently only ``yaml`` is supported.  ``kwargs`` are forwarded to the
    backend constructor (``yaml`` requires ``path: Path``).
    """
    if backend == "yaml":
        path: Path = kwargs.get("path", Path("/etc/mock-idp/config.yaml"))
        return YamlIdentityStore(path)
    raise ValueError(
        f"Unknown store backend {backend!r}. "
        "Supported backends: 'yaml'. "
        "See docs/mock-oidc/ADR-003-store-abstraction.md for adding a new backend."
    )


__all__ = ["IdentityStore", "YamlIdentityStore", "create_store"]
