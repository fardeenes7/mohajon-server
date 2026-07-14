#!/bin/sh
set -e

echo "──────────────────────────────────────────────────────"
echo "  Mohajon Backend — Container Startup"
echo "──────────────────────────────────────────────────────"

# Run migrations and collectstatic only for the web process.
# Celery workers and beat skip this to avoid OOM boot storms when all
# containers start simultaneously and to prevent migration races.
#
# Detected processes: python (manage.py runserver / dev), gunicorn (prod ASGI
# via UvicornWorker), uvicorn (bare uvicorn if used directly), daphne.
case "$1" in
    python|gunicorn|uvicorn|daphne)
        echo "⏳ Running database migrations..."
        python manage.py migrate --noinput

        echo "⏳ Collecting static files..."
        python manage.py collectstatic --noinput --clear
        ;;
esac

echo "✅ Startup complete. Launching: $@"
exec "$@"
