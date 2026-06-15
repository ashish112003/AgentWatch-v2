# =============================================================================
# AgentWatch — Dockerfile
# =============================================================================

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

RUN groupadd --gid 1001 agentwatch \
    && useradd --uid 1001 --gid agentwatch \
       --shell /bin/false \
       --no-create-home \
       agentwatch

WORKDIR /app

COPY --from=builder /install /usr/local

# ── Application source ────────────────────────────────────────────────────────
COPY --chown=agentwatch:agentwatch app/ ./app/
COPY --chown=agentwatch:agentwatch dashboard/ ./dashboard/

# ── Runtime directories ───────────────────────────────────────────────────────
RUN mkdir -p /app/data /app/sandbox \
    && chown -R agentwatch:agentwatch /app/data /app/sandbox

# ── Environment defaults ──────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL="sqlite+aiosqlite:////app/data/agentwatch.db" \
    APP_ENV="production" \
    APP_HOST="0.0.0.0" \
    APP_PORT="8000" \
    LOG_LEVEL="INFO"

USER agentwatch

EXPOSE 8000

# ── Health check ──────────────────────────────────────────────────────────────
HEALTHCHECK \
    --interval=30s \
    --timeout=5s \
    --start-period=20s \
    --retries=3 \
    CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://localhost:8000/health',timeout=4); sys.exit(0 if r.status==200 else 1)"

# ── Start FastAPI ─────────────────────────────────────────────────────────────
CMD sh -c "uvicorn app.main:app \
             --host ${APP_HOST} \
             --port ${APP_PORT} \
             --workers 1"