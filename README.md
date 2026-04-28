# Mohajon Backend — Production Guide

This repository contains the modular monolith backend for the Mohajon SaaS platform.

## 🚀 Quick Start (Production)

### 1. Prerequisites

- Docker & Docker Compose (v2.0+)
- Host Architecture: `linux/amd64` (recommended)
- Domain names for API and SSO configured.

### 2. Environment Configuration

Create a `.env` file in this directory based on `.env.example`. Ensure all production secrets are set.

```bash
cp .env.example .env
# Edit .env with your production values
```

### 3. Deploy the Stack

Deploy using the production-specific compose file:

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

### 4. Post-Deployment Steps

Run migrations and collect static files:

```bash
# Run migrations (automatically handles pgvector and extensions)
docker compose -f docker-compose.prod.yml exec api python manage.py migrate --noinput

# Verify Meilisearch indexes
docker compose -f docker-compose.prod.yml exec api python manage.py search_index --rebuild
```

---

## 🛠 Infrastructure Architecture

The production stack uses a highly optimized service layout:

- **Django 5 API**: Gunicorn-powered application server.
- **PgBouncer**: High-performance connection pooling (Transaction mode).
- **PostgreSQL + pgvector**: Core database with AI/Embedding support.
- **Redis**: Caching and Celery result backend.
- **RabbitMQ**: Message broker for distributed task queues.
- **Meilisearch**: Ultra-fast search engine.
- **Celery Workers**: Separated by priority (`high`, `default`, `media`).

---

## ⚠️ Common Troubleshooting

### 1. Database Collation Mismatch

If you see a warning about collation version mismatch (2.41 vs 2.36), run the following command to refresh it:

```bash
docker compose -f docker-compose.prod.yml exec db psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "ALTER DATABASE ${POSTGRES_DB} REFRESH COLLATION VERSION;"
```

### 2. PgBouncer Authentication Failures

If you encounter `SASL authentication failed`, ensure `PGBOUNCER_USERLIST` in `docker-compose.prod.yml` is correctly formatted with quotes:
`PGBOUNCER_USERLIST="${PGBOUNCER_DB_USER}" "${PGBOUNCER_DB_PASSWORD}"`

### 3. Missing pgvector Extension

If migrations fail with `type "vector" does not exist`, ensure the `db` service is using the `pgvector/pgvector:pg16-alpine` image and that the extension is enabled:

```bash
docker compose -f docker-compose.prod.yml exec db psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

---

## 📦 Monitoring & Logs

```bash
# View all logs
docker compose -f docker-compose.prod.yml logs -f

# Check service health
docker compose -f docker-compose.prod.yml ps
```
