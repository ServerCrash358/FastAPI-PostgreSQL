"""
cache.py — a tiny read-through cache over Redis.

"Read-through" pattern:
    1. On a read, look in Redis first.
    2. HIT  → return the cached value, skip Postgres entirely.
    3. MISS → query Postgres, write the result into Redis with a TTL, return it.

Why it matters here:
    - It's the same caching layer the capstone RAG uses (page 3 of the roadmap:
      "Redis cache check" before doing expensive work).
    - It gives us a *real* cache-hit-rate metric for the Grafana dashboard,
      instead of a faked number.

Every hit/miss also increments a Prometheus counter (see metrics.py) so the
dashboard's "cache hit rate" panel has live data.
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

from app.config import get_settings
from app.metrics import CACHE_HITS, CACHE_MISSES

log = logging.getLogger(__name__)


async def create_redis() -> aioredis.Redis:
    """Open the Redis client at startup and verify connectivity (fail fast)."""
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    await client.ping()
    log.info("Redis reachable at %s", settings.redis_url)
    return client


async def close_redis(client: aioredis.Redis) -> None:
    await client.aclose()


async def cache_get(client: aioredis.Redis, key: str) -> str | None:
    """
    Return the cached JSON string for `key`, or None on a miss.

    A network blip to Redis must NOT take down the API — a cache is an
    optimisation, not a source of truth. So we swallow Redis errors, count
    them as a miss, and let the caller fall back to Postgres.
    """
    settings = get_settings()
    if not settings.cache_enabled:
        return None
    try:
        value = await client.get(key)
    except Exception as exc:  # noqa: BLE001 — degrade gracefully on cache failure
        log.warning("cache_get failed for %s: %s", key, exc)
        CACHE_MISSES.inc()
        return None

    if value is None:
        CACHE_MISSES.inc()
        return None
    CACHE_HITS.inc()
    return value


async def cache_set(client: aioredis.Redis, key: str, value: str) -> None:
    """Store a value with the configured TTL. Best-effort; errors are non-fatal."""
    settings = get_settings()
    if not settings.cache_enabled:
        return
    try:
        await client.set(key, value, ex=settings.cache_ttl_seconds)
    except Exception as exc:  # noqa: BLE001
        log.warning("cache_set failed for %s: %s", key, exc)


async def cache_delete(client: aioredis.Redis, key: str) -> None:
    """
    Invalidate a key. Called on update/delete so a stale doc never lingers.
    This is the hard part of caching: keeping it coherent with the DB.
    """
    settings = get_settings()
    if not settings.cache_enabled:
        return
    try:
        await client.delete(key)
    except Exception as exc:  # noqa: BLE001
        log.warning("cache_delete failed for %s: %s", key, exc)


def doc_key(doc_id: str) -> str:
    """Namespaced cache key, e.g. 'doc:3fa85f64-...'."""
    return f"doc:{doc_id}"
