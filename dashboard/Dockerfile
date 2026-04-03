# Multi-stage build: frontend + backend in one image
# Build: docker build -t genlab-dashboard ./dashboard
# Run:   docker run -p 5151:5151 --env-file .env genlab-dashboard

# ── Stage 1: Build frontend ──────────────────────────────────────
FROM node:22-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --production=false
COPY frontend/ .
RUN npm run build

# ── Stage 2: Python backend ──────────────────────────────────────
FROM python:3.13-slim AS backend
WORKDIR /app

# System deps for psycopg3 (libpq) and eventlet
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Install uv for dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy workspace root files needed for resolution
COPY pyproject.toml uv.lock ./
COPY genlab-core/ genlab-core/
COPY dashboard/pyproject.toml dashboard/pyproject.toml
COPY dashboard/server/ dashboard/server/

# Install dashboard + genlab-core deps
RUN uv sync --project dashboard --frozen --no-dev

# Copy built frontend
COPY --from=frontend /app/frontend/dist dashboard/frontend/dist/

# Config files needed at runtime
COPY genlab-core/config/ genlab-core/config/

ENV GENLAB_PROJECT_ROOT=/app
ENV BACKLOG_CONFIG_PATH=/app/genlab-core/config/lists_config.yaml

EXPOSE 5151

RUN addgroup --system app && adduser --system --ingroup app app
USER app

CMD ["uv", "run", "--project", "dashboard", \
     "gunicorn", \
     "--worker-class", "eventlet", \
     "--workers", "1", \
     "--timeout", "120", \
     "--bind", "0.0.0.0:5151", \
     "server.review_server:app"]
