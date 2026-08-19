"""Async psycopg3 connection pool, lifespan-managed by app.main."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import Settings

logger = logging.getLogger("beelieve.api.db")

_pool: AsyncConnectionPool | None = None


async def open_pool(settings: Settings) -> AsyncConnectionPool:
    """Create and open the process-wide connection pool."""
    global _pool
    if _pool is not None:
        return _pool
    _pool = AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        kwargs={"row_factory": dict_row},
        open=False,
        name="beelieve-api",
    )
    await _pool.open()
    logger.info(
        "database pool opened",
        extra={"min_size": settings.db_pool_min_size, "max_size": settings.db_pool_max_size},
    )
    return _pool


async def close_pool() -> None:
    """Close the pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("database pool closed")


def get_pool() -> AsyncConnectionPool:
    """Return the open pool; raises if the lifespan has not run."""
    if _pool is None:
        raise RuntimeError("Database pool is not open; application lifespan has not started")
    return _pool


async def get_db() -> AsyncIterator[AsyncConnection[DictRow]]:
    """FastAPI dependency yielding a pooled connection.

    The connection's transaction is committed on success and rolled back on
    error by the pool's context manager.
    """
    pool = get_pool()
    async with pool.connection() as conn:
        yield conn
