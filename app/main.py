"""
main.py — the application composition root.

Responsibilities:
  - Build resources once at startup, tear them down once at shutdown (lifespan).
  - Wire in middleware (metrics).
  - Mount routers (health, documents).
  - Expose /metrics for Prometheus to scrape.

This is the file uvicorn imports: `uvicorn app.main:app`.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import ORJSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.cache import close_redis, create_redis
from app.config import get_settings
from app.db import close_pool, create_pool
from app.metrics import PrometheusMiddleware
from app.routers import documents, health

settings = get_settings()

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    The modern FastAPI startup/shutdown hook (replaces @app.on_event).

    Everything before `yield` runs once at startup; everything after runs once
    at graceful shutdown. We stash the pool and redis client on app.state so
    dependencies (get_conn, get_redis) can reach them.

    Critically, the pool is created HERE, not at import time — importing the
    module must have no side effects (so tests and `--reload` stay fast/clean).
    """
    log.info("Starting %s (env=%s)", settings.app_name, settings.app_env)
    app.state.pool = await create_pool()
    app.state.redis = await create_redis()
    try:
        yield
    finally:
        log.info("Shutting down — closing pool and redis")
        await close_pool(app.state.pool)
        await close_redis(app.state.redis)


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    default_response_class=ORJSONResponse,  # orjson is faster than the stdlib json encoder
    lifespan=lifespan,
)

# Middleware runs for every request (see metrics.py).
app.add_middleware(PrometheusMiddleware)

# Routers — each is a self-contained group of endpoints.
app.include_router(health.router)
app.include_router(documents.router)


@app.get("/metrics", include_in_schema=False)
async def metrics() -> PlainTextResponse:
    """
    Prometheus scrape endpoint. generate_latest() renders all registered
    metrics in the text exposition format Prometheus understands.
    The k8s Deployment annotates this path so Prometheus auto-discovers it.
    """
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"service": settings.app_name, "docs": "/docs", "metrics": "/metrics"}
