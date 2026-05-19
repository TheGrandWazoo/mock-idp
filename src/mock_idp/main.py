import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from watchfiles import awatch

from . import config as _cfg
from . import ratelimit as _rl
from .routers import admin, debug, oidc, playground

_log = logging.getLogger(__name__)

_METRICS_ENABLED = os.getenv("MOCK_IDP_METRICS_ENABLED", "").lower() in ("1", "true", "yes")


async def _watch_config() -> None:
    """Background task: reload config whenever the backing file changes.

    watchfiles uses OS-level file-system events (inotify on Linux) so it
    picks up Kubernetes ConfigMap remounts within the kubelet sync window
    (~1 min by default) without polling.

    Only started for the YAML backend — Postgres reloads via POST /admin/reload-config.
    """
    _log.info("Config hot-reload active — watching %s", _cfg.CONFIG_PATH)
    async for _ in awatch(str(_cfg.CONFIG_PATH)):
        _log.info("Config file changed — reloading")
        await _cfg.reload_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialise the store (no-op for YAML; creates pool + loads data for Postgres).
    await _cfg.store.startup()

    # Start the file watcher only for file-backed backends.
    task = None
    if _cfg.CONFIG_PATH.exists():
        task = asyncio.create_task(_watch_config())
    else:
        _log.info(
            "Config path %s not found — file hot-reload disabled. "
            "Use POST /admin/reload-config to reload identity data.",
            _cfg.CONFIG_PATH,
        )

    yield

    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await _cfg.store.shutdown()


app = FastAPI(title="Mock IDP", version="0.6.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cfg.CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)

app.include_router(oidc.router)
app.include_router(debug.router)
app.include_router(admin.router)
app.include_router(playground.router)

if _rl.ENABLED and _rl.limiter is not None:
    from slowapi.errors import RateLimitExceeded  # noqa: PLC0415
    from slowapi.middleware import SlowAPIMiddleware  # noqa: PLC0415
    from starlette.requests import Request as _Request  # noqa: PLC0415
    from starlette.responses import JSONResponse as _JSONResponse  # noqa: PLC0415

    async def _rate_limit_handler(_request: _Request, exc: RateLimitExceeded) -> _JSONResponse:
        retry_after = getattr(exc, "retry_after", 60)
        return _JSONResponse(
            status_code=429,
            content={"error": "rate_limit_exceeded", "detail": str(exc)},
            headers={"Retry-After": str(retry_after)},
        )

    app.state.limiter = _rl.limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    app.add_middleware(SlowAPIMiddleware)
    _log.info("Rate limiting enabled: %s (per source IP)", _rl.RATE_LIMIT)

if _METRICS_ENABLED:
    try:
        from prometheus_fastapi_instrumentator import Instrumentator  # noqa: PLC0415
        Instrumentator().instrument(app).expose(app)
        _log.info("Prometheus metrics enabled at /metrics")
    except ImportError:
        _log.warning(
            "MOCK_IDP_METRICS_ENABLED=true but prometheus-fastapi-instrumentator is not installed. "
            "Install with:  uv sync --extra metrics"
        )
