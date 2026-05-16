"""Alembic async environment for mock-idp Postgres migrations.

Uses SQLAlchemy's async engine (postgresql+asyncpg driver) solely for
Alembic's connection management. The application itself uses asyncpg
directly — no SQLAlchemy ORM is in use.

Usage:
    MOCK_IDP_PG_DSN=postgresql://user:pass@host/mock_idp \\
        uv run alembic upgrade head
"""

import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine


def _get_url() -> str:
    url = os.environ.get("MOCK_IDP_PG_DSN", "")
    if not url:
        raise RuntimeError(
            "MOCK_IDP_PG_DSN is not set. "
            "Export it before running alembic:\n"
            "  export MOCK_IDP_PG_DSN=postgresql://user:pass@host/mock_idp"
        )
    # Normalise to the asyncpg SQLAlchemy dialect.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine = create_async_engine(_get_url())
    async with engine.begin() as conn:
        await conn.run_sync(_do_run_migrations)
    await engine.dispose()


def run_migrations_offline() -> None:
    context.configure(url=_get_url(), target_metadata=None, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
