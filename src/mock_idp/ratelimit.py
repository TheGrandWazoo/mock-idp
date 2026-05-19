"""Optional in-app rate limiting via slowapi.

Enabled by setting MOCK_IDP_RATE_LIMIT to a limit string (e.g. "60/minute").
When the env var is unset the module exports no-op stubs so oidc.py can
import them unconditionally without changing behaviour.

Install the extras before enabling:
    uv sync --extra ratelimit
"""

from __future__ import annotations

import os

RATE_LIMIT = os.getenv("MOCK_IDP_RATE_LIMIT", "").strip()
ENABLED = bool(RATE_LIMIT)

if ENABLED:
    try:
        from slowapi import Limiter
        from slowapi.util import get_remote_address

        limiter = Limiter(key_func=get_remote_address)
        limit = limiter.limit(RATE_LIMIT)
    except ImportError:
        import logging
        logging.getLogger(__name__).warning(
            "MOCK_IDP_RATE_LIMIT is set but slowapi is not installed. "
            "Rate limiting is disabled. Install with:  uv sync --extra ratelimit"
        )
        ENABLED = False
        limiter = None  # type: ignore[assignment]

        def limit(f):  # type: ignore[misc]
            return f

else:
    limiter = None  # type: ignore[assignment]

    def limit(f):  # type: ignore[misc]
        return f
