import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from watchfiles import awatch

from . import config as _cfg
from .routers import admin, debug, oidc, playground

_log = logging.getLogger(__name__)


async def _watch_config() -> None:
    """Background task: reload config whenever the backing file changes.

    watchfiles uses OS-level file-system events (inotify on Linux) so it
    picks up Kubernetes ConfigMap remounts within the kubelet sync window
    (~1 min by default) without polling.
    """
    _log.info("Config hot-reload active — watching %s", _cfg.CONFIG_PATH)
    async for _ in awatch(str(_cfg.CONFIG_PATH)):
        _log.info("Config file changed — reloading")
        _cfg.reload_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if _cfg.CONFIG_PATH.exists():
        task = asyncio.create_task(_watch_config())
    else:
        _log.warning(
            "Config path %s does not exist — hot-reload disabled", _cfg.CONFIG_PATH
        )
    yield
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Mock IDP", version="0.3.9", lifespan=lifespan)
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
