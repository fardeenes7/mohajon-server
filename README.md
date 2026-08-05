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
docker compose -f docker-compose.yml up --build
```

Services started: `db` (Postgres 16 + pgvector), `redis`, `web` (Django runserver), `celery_worker`, `celery_worker_ai`, `celery_beat`.

Postgres is exposed at `localhost:5432`, Redis at `localhost:6379` for local tooling.

### 4. Common commands

```bash
# Run migrations
docker compose -f docker-compose.yml exec web python manage.py migrate

# Open Django shell
docker compose -f docker-compose.yml exec web python manage.py shell

# Create superuser
docker compose -f docker-compose.yml exec web python manage.py createsuperuser

# Run tests
docker compose -f docker-compose.yml exec web pytest
```

---

## Quick Start (Production)

### 1. Prerequisites

- Docker & Docker Compose v2+ on a Linux VPS
- Coolify (handles reverse proxy, SSL, env injection)

### 2. Environment

Set all variables from `.env.example` in Coolify's Environment Variables panel. Do **not** create a `.env` file on the server — Coolify injects them directly.

### 3. Deploy

Images are built and pushed to GHCR by `.github/workflows/build-backend-image.yml`
on every push to `main` (`:latest`) and `dev` (`:dev`). Production pulls the
pre-built image — there is no build step on the server.

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Services started: `web` (Gunicorn + UvicornWorker ASGI), `celery_worker`, `celery_worker_ai`, `celery_beat`.

Postgres and Redis are provisioned as Coolify-managed services and reached via
`DATABASE_URL` / `REDIS_URL` — they are not part of this compose file.

### 4. Post-deployment

Migrations run automatically when `web` starts (via `docker/entrypoint.sh`). To run manually:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py migrate --noinput
```

---

## Infrastructure

| Layer | Service | Notes |
|---|---|---|
| Database | `pgvector/pgvector:pg16` | `vector` extension enabled at init |
| Connection pool | psycopg3 built-in pool | **Prod only** (`DB_POOL_ENABLED=True`). In-process. Requires `CONN_MAX_AGE=0`. |
| Cache / Broker | `redis:7-alpine` | Shared by Celery (db 1) and Django cache (db 0) |
| Web server | Gunicorn + UvicornWorker | ASGI, 4 workers. Django Channels ready. |
| Workers | Celery (2 containers) | General queues + AI queues (thread pool) |
| Scheduler | Celery Beat | DatabaseScheduler — schedules live in Postgres |

---

## Troubleshooting

These use the dev `db` service. In prod, Postgres is Coolify-managed — connect
through Coolify's own database terminal instead.

### Database collation version mismatch

```bash
docker compose exec db \
  psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} \
  -c "ALTER DATABASE ${POSTGRES_DB} REFRESH COLLATION VERSION;"
```

### pgvector extension missing

The `docker/postgres/init/01-create-accounts.sh` init script runs `CREATE EXTENSION IF NOT EXISTS vector;` on first DB creation. If the volume was created before the script existed:

```bash
docker compose exec db \
  psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### Connection pooling errors (prod)

See [`PRODUCTION_DB_POOLING.md`](PRODUCTION_DB_POOLING.md) for `ImproperlyConfigured`
(pooling vs. `CONN_MAX_AGE`), `PoolTimeout`, and how to roll pooling back.

### View logs

```bash
# Dev
docker compose -f docker-compose.yml logs -f

# Prod
docker compose -f docker-compose.prod.yml logs -f
docker compose -f docker-compose.prod.yml ps
```
