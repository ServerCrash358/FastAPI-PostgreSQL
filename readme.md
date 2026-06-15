# Documents API

A production-style **FastAPI** service for storing and retrieving documents,
backed by **async Postgres** (asyncpg) with a **Redis** read-through cache,
fully instrumented for **Prometheus/Grafana**, and shipped to **Kubernetes**
via a **GitHub Actions → ArgoCD (GitOps)** pipeline.

---

## Features

- **Async REST API** — full CRUD over a `documents` table (title, content, JSONB metadata).
- **Connection pooling** — asyncpg pool with a `yield` dependency, so connections are always returned.
- **Read-through cache** — Redis caches `GET /documents/{id}`; writes invalidate it. Degrades gracefully if Redis is down.
- **Typed config** — pydantic-settings loads and validates all config from the environment, failing fast at boot.
- **Health probes** — separate liveness (`/health/live`, no DB) and readiness (`/health/ready`, checks DB) endpoints.
- **Metrics** — Prometheus middleware records request latency (histogram), request counts, and cache hit/miss counters, with bounded label cardinality.
- **Containerized** — multi-stage Dockerfile (non-root runtime), one-command local stack via docker-compose.
- **Kubernetes-ready** — Deployment with startup/liveness/readiness probes, HPA autoscaling, Service, Ingress, ConfigMap/Secret.
- **CI/CD** — GitHub Actions tests against real Postgres+Redis, builds and pushes a SHA-tagged image, and bumps the manifest for ArgoCD to sync.
- **Observability** — Grafana SLO dashboard (p50/p95/p99 latency, error rate, cache hit rate, pod count) and Prometheus alert rules (error rate > 1% for 5m).

---

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/documents` | Create a document |
| `GET` | `/documents/{id}` | Fetch one (cache → DB) |
| `GET` | `/documents` | List, paginated (`?limit=&offset=`) |
| `PATCH` | `/documents/{id}` | Partial update (invalidates cache) |
| `DELETE` | `/documents/{id}` | Delete (invalidates cache) |
| `GET` | `/health/live` | Liveness — process is up |
| `GET` | `/health/ready` | Readiness — DB reachable |
| `GET` | `/metrics` | Prometheus exposition |
| `GET` | `/docs` | Interactive OpenAPI UI |

---

## Architecture

```
client → Ingress → Service → Deployment (2–10 pods, HPA)
                                  │  each pod:
                                  │    FastAPI (uvicorn, PID 1)
                                  │      ├── asyncpg pool ──→ Postgres
                                  │      ├── redis cache  ──→ Redis
                                  │      └── /metrics ──→ Prometheus → Grafana + Alertmanager
```

`GET /documents/{id}` path: `Redis HIT → return` · else `Postgres → fill cache → return`.

---

## Project layout

```
app/                      FastAPI application
  config.py               pydantic-settings (typed env config)
  db.py                   asyncpg pool lifecycle + yield dependency
  cache.py                Redis read-through cache
  metrics.py              Prometheus metrics + ASGI middleware
  schemas.py              pydantic request/response models
  routers/health.py       liveness + readiness
  routers/documents.py    CRUD
  main.py                 app composition (lifespan, middleware, routes)
migrations/001_init.sql   documents table + indexes
Dockerfile                multi-stage build, non-root runtime
docker-compose.yml        local stack (api + db + redis [+ observability profile])
tests/                    pytest suite against real Postgres+Redis
k8s/                      Deployment, Service, HPA, Ingress, ConfigMap, Secret
argocd/application.yaml   ArgoCD GitOps app
.github/workflows/        CI/CD pipeline
observability/
  prometheus/             scrape config + alert rules
  grafana/                SLO dashboard + provisioning
```

---

## Run locally

```bash
# App only (api + db + redis):
docker compose up --build -d

# App + Prometheus + Grafana:
docker compose --profile observability up --build -d
```

Then:

```bash
curl localhost:8000/health/ready
curl -X POST localhost:8000/documents \
  -H 'content-type: application/json' \
  -d '{"title":"first","content":"hello","metadata":{"k":"v"}}'
curl localhost:8000/documents
```

| Service | URL |
|---------|-----|
| API docs | http://localhost:8000/docs |
| Metrics | http://localhost:8000/metrics |
| Grafana dashboard | http://localhost:3000/d/documents-api-slo |
| Prometheus | http://localhost:9090 |

Stop: `docker compose --profile observability down`

### Tests

```bash
docker compose up -d db redis
uv sync --all-extras
psql postgresql://devuser:devpass@localhost:5432/capstone_dev -f migrations/001_init.sql
uv run pytest -v
```

---

## Deploy to Kubernetes

```bash
# Build & push an image (CI does this automatically on push to main):
docker build -t docker.io/<you>/documents-api:$(git rev-parse --short HEAD) .
docker push  docker.io/<you>/documents-api:$(git rev-parse --short HEAD)

# Apply directly:
kubectl apply -k k8s/

# Or GitOps (recommended): bootstrap ArgoCD once, then it self-syncs:
kubectl apply -f argocd/application.yaml

# Verify rollout & probes:
kubectl -n documents rollout status deploy/documents-api
kubectl -n documents get pods          # READY 1/1 = readiness probe passed
```

### Observability on Kubernetes

Assumes `kube-prometheus-stack` (Prometheus + Grafana + Alertmanager) installed
via Helm. The Deployment's `prometheus.io/scrape` annotations let Prometheus
discover the pods; `observability/prometheus/alerts.yaml` (a `PrometheusRule`)
loads the error-rate alert; import `observability/grafana/dashboard.json` into
Grafana.

---

## Configuration

All config is read from environment variables (see `.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql://devuser:devpass@localhost:5432/capstone_dev` | asyncpg DSN |
| `POOL_MIN_SIZE` / `POOL_MAX_SIZE` | `2` / `10` | connection pool bounds |
| `COMMAND_TIMEOUT` | `30` | per-query timeout (s) |
| `REDIS_URL` | `redis://localhost:6379/0` | cache connection |
| `CACHE_TTL_SECONDS` | `60` | cached document freshness |
| `CACHE_ENABLED` | `true` | bypass cache when `false` |

> When running via docker-compose, the `api` container reads config from the
> compose `environment:` block (hosts are the service names `db`/`redis`), not
> from `.env`. The `.env` file is for running the app or tests on the host.
