> **Note:** This architecture document currently lives in `server/` because there is no monorepo git root. If a monorepo root is ever initialized, move this file to the root.

# MOHAJON.md

Internal reference for infra/stack decisions on this project. Keep this updated as decisions change.

---

## Stack Decisions

- **Database:** PostgreSQL 16, single instance, doing double duty:
  - `pgvector` extension for vector/embedding search (product similarity, RAG)
  - Native Postgres full-text search (`SearchVector`/`SearchRank`, `pg_trgm` trigram similarity) for keyword search
  - No separate vector DB (Qdrant) or search engine (Meilisearch) — deliberately deferred until scale/feature needs require it
- **Redis:** Single self-hosted instance (`redis:7-alpine`), shared by:
  - Celery broker (db 1)
  - Celery result backend (db 0, via `REDIS_URL`)
  - Django cache (`django-redis`, db 0)
  - Django Channels channel layer (when wired up)
- **Background jobs:** Celery + Celery Beat (DatabaseScheduler)
- **Real-time:** Django Channels (ASGI, not yet wired — `mohajon/asgi.py` is a plain `get_asgi_application()` today)
- **ASGI server:** Gunicorn + `uvicorn.workers.UvicornWorker` against `mohajon.asgi:application`
  - Chosen over bare Daphne: Uvicorn is more actively maintained and has better performance; Gunicorn adds graceful restart and worker lifecycle management
  - No changes needed when Django Channels is wired up — UvicornWorker already speaks ASGI natively
- **Deployment:** Docker Compose, deployed via Coolify, single VPS. Budget target: ~$20-25/month total infra until 10 paying customers.
- **Connection pooling:** PgBouncer (Bitnami image) in **prod only** — transaction mode. Not used in dev (direct Postgres connection).
  - `CONN_MAX_AGE` **must be `0`** in all prod Django/Celery services when going through PgBouncer transaction mode. Persistent connections exhaust the pool and cause `FATAL: connection` errors.

---

## Why this stack

Single developer, pre-revenue, budget-constrained. Optimized for minimum number of moving pieces to operate and monitor, not for scale we don't have yet.

**Before this decision:** the stack included RabbitMQ (broker), Meilisearch (search), and separate PgBouncer credentials users — 13+ Docker services. Eliminated to reduce operational surface area and RAM footprint on the VPS.

**Current service count:**
- Dev: 6 services (db, redis, web, celery_worker, celery_worker_ai, celery_beat)
- Prod: 7 services (+ pgbouncer)

---

## When to revisit

| Trigger | Action |
|---|---|
| Read replicas needed / PITR / query load saturates Postgres | Split to managed Postgres hosting (Neon, Supabase, RDS) |
| Search UX (typo tolerance, facets) becomes a product differentiator | Add Meilisearch — the catalog code still has the client + fallback, just missing the service |
| ANN tuning needs outgrow pgvector at current vector counts | Add Qdrant as a dedicated vector store |
| Channels concurrency or Celery throughput saturates the box | Scale vertically first, then evaluate worker separation or horizontal scaling |
| Multiple services need independent scaling | Move off Docker Compose monolith → Kubernetes or Fly.io |

---

## Celery Queue Layout

| Worker container | Queues handled | Pool | Concurrency |
|---|---|---|---|
| `celery_worker` | `default`, `high_priority`, `media_processing` | prefork | 4 (prod), 2 (dev) |
| `celery_worker_ai` | `ai_rag`, `ai_copy`, `ai_image` | threads | 4 (both) |

AI worker uses the **thread pool** because tasks spend the vast majority of their time waiting on OpenAI API responses (pure I/O). Threads share memory (OpenAI client singleton, model caches) and avoid fork overhead. The GIL is not a concern for I/O-bound work.

## AI and Chat App Architecture (v0.8+)

The platform architecture splits AI generation and Chat mechanics into two distinct Django apps:
- **`ai` App:** A standalone app containing all AI logic (Model Registry, Usage Logs, Credit deduction logic). It exposes a clean facade (`ai.services.generate_description`, `ai.services.chat_engine`) so other apps never interact directly with OpenAI clients or underlying credit math. The credit package/top-up models remain in `billing`.
- **`chat` App:** A channel-agnostic messaging app storing `Conversation` and `ChatMessage` models. It handles RAG, context windowing, and tool execution. 
  - Uses the **Channel Adapter Pattern** (`chat/channels/*.py`). Channels like Facebook Messenger translate external webhooks into standard `ChatMessage` items and forward them to `chat.services.engine`. This allows easily dropping in new channels (e.g., WhatsApp, Website Widget) without duplicating AI logic.

---

## Working Agreement for AI Agents / Contributors

- **Always use `docker compose exec` for running Python/Django management commands.** Do not run `python manage.py ...`, `pip install ...`, `celery ...` etc. directly on the host. Always go through the running container, e.g.:
  - `docker compose -f docker-compose.dev.yml exec web python manage.py migrate`
  - `docker compose -f docker-compose.dev.yml exec web python manage.py shell`
  - `docker compose -f docker-compose.dev.yml exec web python manage.py createsuperuser`
  - `docker compose -f docker-compose.dev.yml exec web pytest`
- Never assume a local virtualenv is the source of truth — the containers are.
- When adding a new dependency, add it to `pyproject.toml` and rebuild the image (`docker compose -f docker-compose.dev.yml build`), don't `pip install` inside a running container as a permanent fix.
- Dev and prod compose files are separate on purpose — do not merge them or rely on `docker-compose.override.yml` magic without discussing it here first.
- `CONN_MAX_AGE` must remain `"0"` in all prod service definitions. Never increase this value while PgBouncer is in transaction mode.
- CI (`build-backend-image.yml`) builds the `prod` target of the multi-stage `Dockerfile`. The `dev` target is never pushed to the registry.
\n### Technical Debt (AI/Chat Refactor - July 2026)\n- **Greeting keyword pre-filter logic (`chat.services.greeting.py`)**: Untested. (Lost during messenger->chat app split).\n- **FAQEntry & ChatMessage schema validation**: Untested. Needs basic field constraints/creation testing ported from old messenger app.\n- **Facebook comment de-duplication (`chat.services.comment_autoreply.py`)**: Untested. Core auto-reply pipelines need to be validated in the new channel adapter structure.
