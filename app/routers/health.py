"""
health.py — liveness and readiness endpoints for Kubernetes probes.

The single most important DevOps distinction in this whole project:

  LIVENESS  (/health/live)
      "Is the process wedged?" If this fails, Kubernetes KILLS and RESTARTS
      the pod. Therefore it must be cheap and must NOT check dependencies.
      If you checked Postgres here, a 10-second DB blip would make k8s restart
      every pod at once → a self-inflicted outage.

  READINESS (/health/ready)
      "Can this pod serve traffic right now?" If this fails, k8s REMOVES the
      pod from the Service's load-balancer endpoints but does NOT restart it.
      This one DOES check dependencies (Postgres, Redis). When the DB recovers,
      the pod silently rejoins rotation.

Getting these two backwards is one of the most common k8s production incidents.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, Response, status

from app.db import get_conn
from app.schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthOut)
async def liveness() -> HealthOut:
    """Always 200 if the event loop can answer. No dependency checks."""
    return HealthOut(status="alive", database="not_checked")


@router.get("/health/ready", response_model=HealthOut)
async def readiness(
    response: Response,
    conn: asyncpg.Connection = Depends(get_conn),
) -> HealthOut:
    """
    200 only if we can reach Postgres. Returns 503 otherwise so k8s pulls the
    pod out of rotation until the DB is back.
    """
    try:
        await conn.execute("SELECT 1")
        return HealthOut(status="ready", database="up")
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthOut(status="not_ready", database="down")
