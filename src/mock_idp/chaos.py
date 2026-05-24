"""In-memory per-issuer chaos fault store.

Faults are intentionally not persisted — a fresh start clears all injected
failures, which matches the expected behaviour for a test fixture.
"""
from __future__ import annotations

import asyncio
import random
import threading
from typing import Any

from fastapi import HTTPException

_VALID_MODES = frozenset(
    {"error_500", "delay_ms", "invalid_token", "invalid_signature", "expired_token"}
)

_faults: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def set_fault(issuer: str, config: dict[str, Any]) -> None:
    with _lock:
        _faults[issuer] = dict(config)


def clear_fault(issuer: str) -> bool:
    """Remove the fault for *issuer*. Returns True if one was present."""
    with _lock:
        return _faults.pop(issuer, None) is not None


def get_faults() -> dict[str, dict[str, Any]]:
    with _lock:
        return dict(_faults)


def get_fault(issuer: str) -> dict[str, Any] | None:
    with _lock:
        return dict(_faults[issuer]) if issuer in _faults else None


async def apply_pre(issuer: str) -> None:
    """Raise or delay before request processing based on the active fault.

    Handles: error_500, delay_ms.
    Token-corruption modes (invalid_token, invalid_signature, expired_token)
    are applied post-claims-build in token_handler.py via get_fault().
    """
    fault = get_fault(issuer)
    if not fault:
        return
    mode = fault["mode"]
    if mode == "error_500":
        prob = float(fault.get("probability", 1.0))
        if random.random() < prob:
            raise HTTPException(500, detail={"error": "chaos_error_500"})
    elif mode == "delay_ms":
        await asyncio.sleep(float(fault.get("value", 0)) / 1000.0)
