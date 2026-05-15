"""Provider registry — maps provider name to its claims-building module."""

import importlib
from types import ModuleType

_REGISTRY: dict[str, str] = {
    "entra_id": ".entra_id",
}


def get_provider(name: str) -> ModuleType:
    path = _REGISTRY.get(name)
    if path is None:
        raise ValueError(f"Unknown provider {name!r}. Available: {sorted(_REGISTRY)}")
    return importlib.import_module(path, package=__package__)
