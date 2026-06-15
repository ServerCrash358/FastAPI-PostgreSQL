"""
conftest.py — shared pytest fixtures.

These are integration tests: they run the real app against a real Postgres +
Redis. In CI those are provided as "service containers" (see the GitHub Actions
workflow). Locally, run `docker compose up -d db redis` first.

We use FastAPI's TestClient as a context manager (`with TestClient(app)`),
which runs the real lifespan — so the asyncpg pool and Redis client are
created exactly as in production.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture
def client():
    # Entering the context manager triggers lifespan startup (pool + redis);
    # exiting triggers shutdown. Each test gets a fully-wired app.
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_table():
    """
    Truncate the documents table before each test for isolation.

    We open a short-lived standalone connection via asyncio.run() rather than
    reusing the app's pool — the pool lives on the TestClient's own event loop,
    and reaching across loops causes 'attached to a different loop' errors.
    A separate connection in this thread's loop sidesteps that entirely.
    """
    async def _truncate() -> None:
        conn = await asyncpg.connect(get_settings().database_url)
        try:
            await conn.execute("TRUNCATE documents")
        finally:
            await conn.close()

    asyncio.run(_truncate())
    yield
