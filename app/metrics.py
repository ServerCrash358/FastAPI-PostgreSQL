"""
metrics.py — Prometheus metric objects + the ASGI middleware that records them.

The four Grafana panels the roadmap asks for map directly to these metrics:
    p50/p95/p99 latency  ← HTTP_REQUEST_DURATION  (a Histogram)
    error rate           ← HTTP_REQUESTS_TOTAL{status=~"5.."} / total
    cache hit rate       ← CACHE_HITS / (CACHE_HITS + CACHE_MISSES)
    pod count            ← comes from kube-state-metrics, not the app

Metric types 101:
    Counter   — only goes up (request counts, cache hits). You rate() it in PromQL.
    Histogram — bucketed observations (latencies). Prometheus computes quantiles
                from the buckets with histogram_quantile().
    Gauge     — goes up and down (in-flight requests, queue depth).
"""

from __future__ import annotations

import time

from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ── metric definitions ───────────────────────────────────────────────────
# Label cardinality warning: we label by method, path-template, and status.
# We use the *route template* ("/documents/{doc_id}") not the raw path, so a
# million distinct IDs don't explode into a million time series.

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    # Explicit buckets tuned for a web API (1ms … 5s). The default buckets
    # top out at 10s and are coarse; these give meaningful p95/p99.
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

CACHE_HITS = Counter("cache_hits_total", "Read-through cache hits")
CACHE_MISSES = Counter("cache_misses_total", "Read-through cache misses")


class PrometheusMiddleware(BaseHTTPMiddleware):
    """
    Wraps every request to time it and record the result.

    Why middleware (not a decorator per route)?
      It runs for *all* routes automatically — you can't forget to instrument
      a new endpoint. It sits in the ASGI chain, so it sees the final status
      code even when a route raises and an exception handler produces the 5xx.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Use the matched route template, not request.url.path, to keep label
        # cardinality bounded. Unmatched requests collapse to "__unmatched__".
        start = time.perf_counter()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            # An unhandled exception still becomes a 500 to the client; record it.
            status = 500
            raise
        finally:
            elapsed = time.perf_counter() - start
            path = _route_template(request)
            HTTP_REQUESTS_TOTAL.labels(request.method, path, str(status)).inc()
            HTTP_REQUEST_DURATION.labels(request.method, path).observe(elapsed)
        return response


def _route_template(request: Request) -> str:
    # Starlette sets scope["route"] only when a route actually matched, and the
    # route exposes `.path` as the *template* ("/documents/{doc_id}"). We never
    # fall back to request.url.path: an unmatched request (404) carries an
    # attacker- or scanner-controlled URL, and labelling by it would let anyone
    # mint unbounded time series and exhaust Prometheus memory. Collapse every
    # unmatched request into one constant label instead.
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if path is not None else "__unmatched__"
