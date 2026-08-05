# Production Postgres Connection Pooling

This project uses **psycopg3's built-in connection pooling** (Django 5.0+) rather than an external PgBouncer sidecar. The pool lives in-process, so there's no extra network hop and no transaction-mode limitations (server-side cursors work normally).

## How it works

- **Pool location**: in each Python process (gunicorn workers, celery workers, celery beat).
- **Activation**: set `DB_POOL_ENABLED=True` in the environment. Production compose does this automatically.
- **Django constraint**: pooling requires `CONN_MAX_AGE=0`. Django raises `ImproperlyConfigured("Pooling doesn't support persistent connections.")` otherwise — the pool keeps connections alive, so per-request persistence would be redundant.

## Required environment variables

Set these in production (Coolify injects them):

- `DATABASE_URL` — the managed Postgres connection string from Coolify (not routed through pgbouncer)
- `REDIS_URL` — managed Redis connection string
- `CELERY_BROKER_URL` — typically `${REDIS_URL}/1`
- `DB_POOL_ENABLED=True` — turns on pooling
- `CONN_MAX_AGE=0` — required by Django when pooling (enforced in settings.py)
- `SECRET_KEY`, `ALLOWED_HOSTS`, etc. — standard Django config

### Pool sizing (optional, defaults provided)

Each service in docker-compose.prod.yml has its own pool size env vars with sensible defaults:

```bash
# Web service — gunicorn, 4 processes, each with its own pool
WEB_DB_POOL_MIN_SIZE=2    # default
WEB_DB_POOL_MAX_SIZE=10   # default

# Celery worker — prefork, concurrency 4, each child has its own pool
WORKER_DB_POOL_MIN_SIZE=1
WORKER_DB_POOL_MAX_SIZE=2

# Celery AI worker — threads, concurrency 4, all sharing ONE pool
AI_WORKER_DB_POOL_MIN_SIZE=2
AI_WORKER_DB_POOL_MAX_SIZE=6

# Celery beat — single process
BEAT_DB_POOL_MIN_SIZE=1
BEAT_DB_POOL_MAX_SIZE=2
```

**Capacity rule**: total connections ≈ (number of processes) × max_size.

- Web: gunicorn runs 4 workers → 4 × 10 = 40 connections max
- Celery worker: prefork concurrency 4, one pool per child → 4 × 2 = 8 max
- Celery AI worker: threads share a single process → 1 × 6 = 6 max
- Celery beat: single process → 1 × 2 = 2 max

Ceiling across the stack: ~56 connections. Keep the sum below your managed
Postgres connection limit (typically 100+ on hosted plans).

**Prefork vs. threads** — this is the distinction that bites. With `--pool
prefork`, each child process builds its own pool, so `max_size=1` per child is
already enough for single-threaded task execution. With `--pool threads`, every
thread draws from one shared pool, so `max_size` must be **greater than**
`--concurrency` or a saturated moment blocks until `DB_POOL_TIMEOUT` fires.

## Start the stack

From `server/`:

```bash
docker compose -f docker-compose.prod.yml up -d
```

Coolify handles this automatically when you connect the repository and configure the environment panel.

## Verify health

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail=50 web celery_worker
```

Expected:
- All services show `Up` and `healthy` (or running, if no healthcheck)
- Web logs show `Booting Gunicorn` and `Listening at: http://0.0.0.0:8000`
- Worker logs show `celery@... ready.`
- No `ImproperlyConfigured` errors about pooling

## Safe migration flow

Migrations run automatically on web container startup (see `docker/entrypoint.sh`). To run manually:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py migrate
```

## Tuning the pool

Beyond the basic `DB_POOL_{MIN,MAX}_SIZE`, you can override these in the environment:

- `DB_POOL_TIMEOUT` (default: `10`) — seconds to wait for a free connection before raising `PoolTimeout` instead of hanging a request.
- `DB_POOL_MAX_LIFETIME` (default: `1800`) — recycle a connection after this many seconds so long-lived processes don't hold stale server connections indefinitely.

Set them in Coolify's environment panel and restart the affected service.

## Troubleshooting

### `ImproperlyConfigured: Pooling doesn't support persistent connections.`

`CONN_MAX_AGE` is non-zero. Set it to `0` — the pool holds connections, Django must not also persist them per-request.

### `Error loading psycopg_pool module. Did you install psycopg[pool]?`

The lock file is missing `psycopg-pool`. Run `poetry lock` and rebuild the image.

### `PoolTimeout: couldn't get a connection after 10.0 sec`

The pool is saturated — all connections are in use and none freed within the timeout.

- **Short-term**: raise `DB_POOL_MAX_SIZE` (but watch total connections vs. Postgres limit).
- **Root cause**: slow queries or tasks that hold transactions open for a long time. Profile with `django-silk` or `pg_stat_activity`.

### Rollback: disable pooling

Set `DB_POOL_ENABLED=False` and `CONN_MAX_AGE=60` (or another non-zero value for traditional persistent connections). Restart all services. The pool code path is bypassed entirely.
