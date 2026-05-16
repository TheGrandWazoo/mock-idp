"""Webhook delivery — fire-and-forget HTTP POST on token issuance.

Configured via the top-level ``webhooks:`` list in the YAML config:

    webhooks:
      - url: http://test-recorder.example.com/events
        events: [token_issued]
        timeout_seconds: 5

Delivery is best-effort: failures are logged at WARNING level and never
propagate to the caller. Token issuance always succeeds regardless of
whether any webhook endpoint is reachable.
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from . import config as _cfg

_log = logging.getLogger(__name__)


async def fire_webhooks(event: str, payload: dict) -> None:
    """Deliver ``event`` to all matching configured webhook URLs.

    ``payload`` is merged into the request body alongside the event name and
    timestamp. Fires all matching webhooks concurrently; errors are swallowed.
    """
    matching = [h for h in _cfg.WEBHOOKS if event in h.events]
    if not matching:
        return

    body = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    await asyncio.gather(
        *[_deliver(h.url, body, h.timeout_seconds) for h in matching],
        return_exceptions=True,
    )


async def _deliver(url: str, body: dict, timeout: float) -> None:
    """POST ``body`` to ``url``. Logs and swallows all errors."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            await client.post(url, json=body)
    except Exception as exc:
        _log.warning("Webhook delivery failed (%s): %s", url, exc)
