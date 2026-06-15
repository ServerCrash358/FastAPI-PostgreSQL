# Documents API — Week 2 + Weeks 3–4 deliverables

A Dockerised **FastAPI** service backed by **async Postgres** (asyncpg) and a
**Redis** read-through cache, then deployed to **Kubernetes** via **GitOps
(ArgoCD)**, with a CI/CD pipeline, Prometheus metrics, a Grafana SLO dashboard,
and alerting.

It's deliberately the precursor to the capstone: the `documents` table is the
capstone's table (roadmap p20) minus the `embedding vector(1536)` column you
add in Week 5.

---

## What maps to which roadmap deliverable

| # | Roadmap "what to build" | Where it lives |
|---|---|---|
| — | Dockerised FastAPI app with async Postgres (Week 2) | `app/`, `Dockerfile`, `docker-compose.yml` |
| ① | Deploy on k8s with full readiness/liveness probes | `k8s/deployment.yaml` (startup + liveness + readiness) |
| ② | CI/CD: push → test → build → push image → ArgoCD syncs | `.github/workflows/ci-cd.yaml`, `argocd/application.yaml` |
| ③ | Grafana dashboard: p50/p95/p99, error rate, cache hit, pod count | `observability/grafana/dashboard.json` |
| ④ | Alert when error rate > 1% for 5 min | `observability/prometheus/alerts.yaml` |

---

## Architecture

```
client → Ingress → Service → Deployment (2–10 pods)
                                  │  each pod:
                                  │    FastAPI (uvicorn, PID 1)
                                  │      ├── asyncpg pool ──→ Postgres
                                  │      ├── redis cache  ──→ Redis
                                  │      └── /metrics ──→ Prometheus → Grafana + Alertmanager
```

Request path for `GET /documents/{id}`:
`Redis HIT → return` · else `Postgres → fill cache → return`.

---

## Run it locally

```bash
# 1. Everything in one command (API + Postgres + Redis):
docker compose up --build

# 2. Try it:
curl localhost:8000/health/ready
curl -X POST localhost:8000/documents \
  -H 'content-type: application/json' \
  -d '{"title":"first","content":"hello","metadata":{"week":2}}'
curl localhost:8000/documents
open http://localhost:8000/docs        # interactive OpenAPI UI
curl localhost:8000/metrics            # Prometheus exposition
```

Run tests against local infra:

```bash
docker compose up -d db redis
uv sync --all-extras
psql postgresql://devuser:devpass@localhost:5432/capstone_dev -f migrations/001_init.sql
uv run pytest -v
```

---

## Deploy to Kubernetes (minikube → EKS)

```bash
# Build & push an image (CI does this automatically on push to main):
docker build -t docker.io/<you>/documents-api:$(git rev-parse --short HEAD) .
docker push  docker.io/<you>/documents-api:$(git rev-parse --short HEAD)

# Option A — apply directly:
kubectl apply -k k8s/

# Option B — GitOps (recommended): bootstrap ArgoCD once, then it self-syncs:
kubectl apply -f argocd/application.yaml

# Verify probes & rollout:
kubectl -n documents rollout status deploy/documents-api
kubectl -n documents get pods          # READY 1/1 means readiness probe passed
```

### Observability stack
Assumes `kube-prometheus-stack` (Prometheus + Grafana + Alertmanager) installed
via Helm. The Deployment's `prometheus.io/scrape` annotations make Prometheus
discover the pods; `observability/prometheus/alerts.yaml` (a `PrometheusRule`)
loads the error-rate alert; import `observability/grafana/dashboard.json` into
Grafana.

---

## The teaching notes (how & why)

- **asyncpg pool, not per-request connections** — opening a PG connection is
  expensive; the pool amortises it. `db.py` yields a pooled connection per
  request via a `yield` dependency so it's always returned, even on error.
- **pydantic-settings** — typed, validated config from env; fails fast at boot.
- **Liveness ≠ readiness** — liveness (`/health/live`, no DB) → restart;
  readiness (`/health/ready`, checks DB) → pull from load balancer. Mixing
  them up causes restart storms. See `k8s/deployment.yaml`.
- **Multi-stage Dockerfile** — build tooling stays in stage 1; the runtime
  image ships only the venv + code. Layer ordering (deps before code) makes
  rebuilds fast.
- **GitOps** — CI commits the desired image tag; ArgoCD reconciles the cluster.
  No `kubectl` from CI. SHA tags, never `:latest`.
- **Prometheus** — `Counter` for counts (rate() them), `Histogram` for latency
  (histogram_quantile() for p50/p95/p99). Label by route template, not raw path,
  to bound cardinality.
- **Alert `for: 5m`** — the condition must hold 5 minutes before firing, so a
  one-off error blip doesn't page anyone at 3am.

---

## Next (Week 5+)
Add `embedding vector(1536)` + an HNSW index to `documents`, an `/ingest`
endpoint that embeds content, and a `/query` endpoint doing ANN search +
cross-encoder rerank → this becomes the capstone RAG API.
