"""
db.py — asyncpg connection pool lifecycle + FastAPI dependency.

Mental model:
  - Opening a Postgres connection is *expensive* (TCP + TLS + auth + backend
    process fork on the server). You never want to do that per-request.
  - A *pool* keeps N connections open and hands them out. A request borrows
    one, runs its queries, and returns it. This is the single biggest
    performance lever in an async web service.

The pool is created once at startup (see main.py lifespan) and stored on
app.state. Routes get a connection via the `get_conn` dependency.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import asyncpg
from fastapi import Request

from app.config import get_settings

log = logging.getLogger(__name__)


async def create_pool() -> asyncpg.Pool:
    """
    Build the connection pool. Called once during app startup.

    min_size connections are opened eagerly; the pool grows up to max_size
    on demand. command_timeout aborts any single query that runs too long.
    """
    settings = get_settings()
    log.info("Creating asyncpg pool (min=%d, max=%d)", settings.pool_min_size, settings.pool_max_size)

    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
        command_timeout=settings.command_timeout,
    )
    # Fail fast: prove we can actually talk to Postgres before serving traffic.
    async with pool.acquire() as conn:
        await conn.execute("SELECT 1")
    log.info("Pool ready — Postgres reachable")
    return pool


async def close_pool(pool: asyncpg.Pool) -> None:
    """Gracefully drain and close the pool at shutdown."""
    log.info("Closing asyncpg pool")
    await pool.close()


async def get_conn(request: Request) -> AsyncIterator[asyncpg.Connection]:
    """
    FastAPI dependency that yields one pooled connection per request.

    Why `yield` (not `return`)?
      A dependency that yields is a context manager: code before `yield` runs
      before the route handler, code after runs *after the response is sent*.
      `async with pool.acquire()` guarantees the connection is returned to the
      pool even if the route raises — no connection leaks.

    Usage in a route:
        async def list_docs(conn: asyncpg.Connection = Depends(get_conn)):
            rows = await conn.fetch("SELECT ...")
    """
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        yield conn
