from contextlib import contextmanager
from django.db import connection

@contextmanager
def rls_bypass():
    """
    Temporarily bypass Row Level Security by setting app.bypass_rls = 'on'.
    Required for internal Celery background tasks or global admin queries.
    """
    with connection.cursor() as cursor:
        cursor.execute("SET app.bypass_rls = 'on'")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SET app.bypass_rls = 'off'")

@contextmanager
def tenant_context(shop_id):
    """
    Temporarily set the active Postgres tenant via app.current_shop_id.
    Required for unauthenticated public APIs (e.g., storefront) to read tenant data.
    """
    with connection.cursor() as cursor:
        cursor.execute("SET app.current_shop_id = %s", [str(shop_id)])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SET app.current_shop_id = ''")
