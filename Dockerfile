# ─── Stage 1: dependency builder ──────────────────────────────────────────────
# Builds the full Python environment. Output is copied into dev/prod stages.
# Kept separate so the heavy build tools (gcc, libpq-dev) never land in the
# final runtime image.
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

ENV POETRY_VERSION=2.1.3 \
    POETRY_HOME=/opt/poetry \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    PIP_NO_CACHE_DIR=1

RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"

WORKDIR /app

COPY pyproject.toml poetry.lock ./
RUN poetry install --no-interaction --no-ansi --no-root --only main


# ─── Stage 2: dev ─────────────────────────────────────────────────────────────
# Lightweight image for local development.
#   - Source code is bind-mounted at runtime (docker-compose.dev.yml).
#   - Runs as root so volume mounts don't cause permission issues on Linux hosts.
#   - Uses Django's built-in ASGI-capable dev server (auto-reloads on save).
#   - No collectstatic (DEBUG=True serves static files directly).
FROM python:3.12-slim AS dev

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]


# ─── Stage 3: prod ────────────────────────────────────────────────────────────
# Hardened production image.
#   - Non-root user (django) for security.
#   - Source code baked in — no bind mounts.
#   - ASGI server: Gunicorn + UvicornWorker.
#       Chosen over bare Uvicorn for Gunicorn's process management (graceful
#       restarts, worker recycling, SIGTERM handling) while still using Uvicorn's
#       ASGI protocol implementation. Chosen over Daphne because Uvicorn is more
#       actively maintained and Django Channels 4.x fully supports it.
#       When Django Channels is wired up (real-time features), no changes needed
#       here — uvicorn.workers.UvicornWorker already speaks ASGI natively.
FROM python:3.12-slim AS prod

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd --system django && useradd --system --gid django --create-home django

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app

# Bake in source — no bind mounts in prod
COPY --chown=django:django . .

# Pre-create writable directories the app needs
RUN mkdir -p /app/staticfiles /app/mediafiles \
    && chown -R django:django /app/staticfiles /app/mediafiles

COPY --chown=django:django docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER django

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
# Gunicorn manages worker lifecycle; UvicornWorker handles ASGI protocol.
# 4 workers is a safe default for a single-CPU VPS (2×CPU+1 rule of thumb).
# Tune via GUNICORN_WORKERS env override if needed in Coolify.
CMD ["gunicorn", "mohajon.asgi:application", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "120", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
