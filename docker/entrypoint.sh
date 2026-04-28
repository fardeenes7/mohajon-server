#!/bin/sh
set -e

echo "──────────────────────────────────────────────────────"
echo "  Mohajon Backend — Container Startup"
echo "──────────────────────────────────────────────────────"

# Only the API (python/gunicorn) should run migrations and collectstatic.
# Celery workers and beat schedulers skip this to avoid OOM boot storms
# when all workers start simultaneously.
if [ "$1" = "python" ] || [ "$1" = "gunicorn" ]; then
    echo "⏳ Running database migrations..."
    python manage.py migrate --noinput

    echo "⏳ Collecting static files..."
    python manage.py collectstatic --noinput --clear
fi

echo "✅ Startup complete. Launching: $@"
exec "$@"
