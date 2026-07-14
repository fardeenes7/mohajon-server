# Mohajon Backend

Modular monolith Django backend for the Mohajon SaaS platform.

For infra/stack decisions, see [`MOHAJON.md`](../MOHAJON.md) at the repo root.

---

## Quick Start (Development)

### 1. Prerequisites

- Docker & Docker Compose v2+

### 2. Environment

```bash
cp .env.dev.example .env
# Optional: fill in real API keys (OpenAI, Meta, AWS) — not needed for basic dev
```

### 3. Start the stack

```bash
docker compose -f docker-compose.dev.yml up --build
```

Services started: `db` (Postgres 16 + pgvector), `redis`, `web` (Django runserver), `celery_worker`, `celery_worker_ai`, `celery_beat`.

Postgres is exposed at `localhost:5432`, Redis at `localhost:6379` for local tooling.

### 4. Common commands

```bash
# Run migrations
docker compose -f docker-compose.dev.yml exec web python manage.py migrate

# Open Django shell
docker compose -f docker-compose.dev.yml exec web python manage.py shell

# Create superuser
docker compose -f docker-compose.dev.yml exec web python manage.py createsuperuser

# Run tests
docker compose -f docker-compose.dev.yml exec web pytest
```

---

## Quick Start (Production)

### 1. Prerequisites

- Docker & Docker Compose v2+ on a Linux VPS
- Coolify (handles reverse proxy, SSL, env injection)

### 2. Environment

Set all variables from `.env.example` in Coolify's Environment Variables panel. Do **not** create a `.env` file on the server — Coolify injects them directly.

### 3. Deploy

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Services started: `db`, `pgbouncer`, `redis`, `web` (Gunicorn + UvicornWorker ASGI), `celery_worker`, `celery_worker_ai`, `celery_beat`.

### 4. Post-deployment

Migrations run automatically when `web` starts (via `docker/entrypoint.sh`). To run manually:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py migrate --noinput
```

---

## Infrastructure

| Layer | Service | Notes |
|---|---|---|
| Database | `pgvector/pgvector:pg16-alpine` | pgvector + pg_trgm extensions enabled at init |
| Connection pool | PgBouncer (Bitnami) | **Prod only.** Transaction mode. `CONN_MAX_AGE=0` required. |
| Cache / Broker | `redis:7-alpine` | Shared by Celery (db 1) and Django cache (db 0) |
| Web server | Gunicorn + UvicornWorker | ASGI, 4 workers. Django Channels ready. |
| Workers | Celery (2 containers) | General queues + AI queues (thread pool) |
| Scheduler | Celery Beat | DatabaseScheduler — schedules live in Postgres |

---

## Troubleshooting

### Database collation version mismatch

```bash
docker compose -f docker-compose.prod.yml exec db \
  psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} \
  -c "ALTER DATABASE ${POSTGRES_DB} REFRESH COLLATION VERSION;"
```

### pgvector extension missing

The `docker/postgres/init/01-create-accounts.sh` init script runs `CREATE EXTENSION IF NOT EXISTS vector;` on first DB creation. If the volume was created before the script existed:

```bash
docker compose -f docker-compose.prod.yml exec db \
  psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### PgBouncer SASL authentication failed (prod)

Ensure `PGBOUNCER_DB_USER` and `PGBOUNCER_DB_PASSWORD` in Coolify match the values used when the Postgres role was created by the init script. The Bitnami PgBouncer image constructs its userlist from these env vars automatically.

### View logs

```bash
# Dev
docker compose -f docker-compose.dev.yml logs -f

# Prod
docker compose -f docker-compose.prod.yml logs -f
docker compose -f docker-compose.prod.yml ps
```
