"""
WebSocket JWT auth middleware for Channels.

The SPA authenticates with `rest_framework_simplejwt` access tokens. A browser
WebSocket cannot set an Authorization header, so the access token is passed in
the query string: `wss://.../ws/chat/?token=<access-jwt>&tenant=<shop_id>`.

This middleware validates that token and sets `scope["user"]`. Tenant membership
is enforced separately in the consumer (a valid user may not belong to the
requested shop).
"""
from __future__ import annotations

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser


@database_sync_to_async
def _get_user_from_token(raw_token: str):
    # Imported lazily so the app registry is ready before models are touched.
    from rest_framework_simplejwt.authentication import JWTAuthentication
    from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

    auth = JWTAuthentication()
    try:
        validated = auth.get_validated_token(raw_token)
        return auth.get_user(validated)
    except (InvalidToken, TokenError, Exception):
        return AnonymousUser()


class JWTAuthMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        query = parse_qs((scope.get("query_string") or b"").decode())
        token = (query.get("token") or [None])[0]

        if token and (scope.get("user") is None or not getattr(scope.get("user"), "is_authenticated", False)):
            scope["user"] = await _get_user_from_token(token)

        scope.setdefault("user", AnonymousUser())
        return await self.inner(scope, receive, send)
