# syntax=docker/dockerfile:1
#
# Multi-stage build — the headline Docker skill from the roadmap
# ("Multi-stage builds for minimal production images").
#
# Stage 1 (builder): has uv + build tooling, resolves and installs deps into a
#                    self-contained virtualenv.
# Stage 2 (runtime): a slim base that copies ONLY the venv + app code. None of
#                    the build tooling ships to production → smaller image,
#                    fewer CVEs, faster pulls.

# ── Stage 1: builder ───────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# uv is the package manager from the roadmap. We grab its static binary from
# the official image rather than pip-installing it (faster, no extra layers).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Copy ONLY the dependency manifests first. Docker caches layers by content,
# so as long as pyproject.toml doesn't change, the (slow) dependency install
# layer is reused even when your app code changes. This is layer caching —
# the reason your CI builds go from minutes to seconds.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /app/.venv && \
    uv pip install --python /app/.venv -r pyproject.toml

# Now copy the application source (changes often → late layer).
COPY app ./app

# ── Stage 2: runtime ───────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Run as a non-root user. If the container is ever compromised, the attacker
# isn't root inside it — a basic but important hardening step.
RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

# Copy the ready-made virtualenv and code from the builder. No uv, no compilers.
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /app/app  /app/app

# Put the venv on PATH so `uvicorn`/`python` resolve to it.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser
EXPOSE 8000

# Exec form (JSON array) so uvicorn is PID 1 and receives SIGTERM directly —
# this is what makes graceful shutdown (lifespan teardown) actually fire when
# Kubernetes stops the pod.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
