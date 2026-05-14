from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config as _cfg
from .routers import admin, debug, oidc, playground

app = FastAPI(title="Mock IDP", version="0.2.0")
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
