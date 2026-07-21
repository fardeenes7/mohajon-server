"""
ASGI config for mohajon project.

Serves both HTTP (Django) and WebSocket (Channels) on the same ASGI app. Prod
runs this via `gunicorn mohajon.asgi:application --worker-class uvicorn.workers.UvicornWorker`.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mohajon.settings')

# get_asgi_application() must run before importing anything that touches the
# app registry (consumers, models), so the http app is built first.
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

import chat.routing  # noqa: E402
from chat.ws_auth import JWTAuthMiddleware  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    # JWTAuthMiddleware resolves ?token=<access-jwt> into scope["user"];
    # AuthMiddlewareStack is the outer fallback so session-cookie auth
    # (dj_rest_auth) also works during local development.
    "websocket": AuthMiddlewareStack(
        JWTAuthMiddleware(
            URLRouter(chat.routing.websocket_urlpatterns)
        )
    ),
})
