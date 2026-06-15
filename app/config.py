"""
config.py — typed application settings, loaded from environment variables.

Why pydantic-settings instead of os.getenv() everywhere?
  - One typed object (`settings`) is the single source of truth for config.
  - Values are validated and coerced at startup: if DATABASE_URL is missing
    or POOL_MAX is "abc", the app fails *immediately and loudly* rather than
    blowing up on the first request.
  - Follows the 12-factor app rule: config lives in the environment, never in code.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # model_config tells pydantic where/how to read values.
    #   env_file=".env"     → also read a local .env file (handy for dev; ignored in prod)
    #   env_prefix=""       → env var names map 1:1 to field names (case-insensitive)
    #   extra="ignore"      → don't crash if the environment has unrelated vars
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── application ──────────────────────────────────────────────────────
    app_name: str = "documents-api"
    app_env: str = "development"        # development | staging | production
    log_level: str = "info"

    # ── database ─────────────────────────────────────────────────────────
    # asyncpg wants a DSN like: postgresql://user:pass@host:5432/dbname
    database_url: str = Field(
        default="postgresql://devuser:devpass@localhost:5432/capstone_dev",
        description="Postgres DSN consumed by asyncpg.create_pool()",
    )

    # Connection-pool sizing. Too small → requests queue waiting for a connection.
    # Too large → you exhaust Postgres' max_connections (default 100) across replicas.
    pool_min_size: int = 2
    pool_max_size: int = 10

    # Statement timeout (seconds) applied to every query — a safety net so a
    # runaway query can't hold a connection forever.
    command_timeout: float = 30.0

    # ── redis (read-through cache) ───────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 60          # how long a cached document stays fresh
    cache_enabled: bool = True           # flip off to bypass cache entirely


@lru_cache
def get_settings() -> Settings:
    """
    Return a cached Settings instance.

    @lru_cache makes this a singleton: pydantic reads the environment exactly
    once, on first call. Every later call returns the same object. This matters
    because (a) parsing env is wasteful to repeat, and (b) FastAPI's dependency
    system will call this on each request — caching keeps that free.
    """
    return Settings()
