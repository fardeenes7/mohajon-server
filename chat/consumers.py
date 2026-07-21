"""
Agent inbox WebSocket consumer.

One connection per open agent dashboard. All agents of a shop share the group
`chat_shop_<shop_id>`, so a single message event fans out to every open
dashboard for that shop. This group *is* the tenant isolation boundary — a
connection may only join a shop it is a member of.

Client connects to:  wss://.../ws/chat/?token=<access-jwt>&tenant=<shop_id>

Server → client events (JSON):
  {"type": "chat_message",        "payload": {... message ...}}
  {"type": "conversation_update", "payload": {... conversation summary ...}}

Client → server (optional):
  {"action": "mark_read", "channel": "...", "psid": "..."}
  {"action": "ping"}
"""
from __future__ import annotations

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer


def shop_group_name(shop_id: str) -> str:
    return f"chat_shop_{shop_id}"


class AgentInboxConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if user is None or not getattr(user, "is_authenticated", False):
            await self.close(code=4401)
            return

        query = parse_qs((self.scope.get("query_string") or b"").decode())
        shop_id = (query.get("tenant") or [None])[0]
        if not shop_id:
            await self.close(code=4400)
            return

        # Never trust the client-supplied tenant — verify membership, mirroring
        # core.middleware.TenantMiddleware.
        if not await self._is_member(user_id=user.id, shop_id=shop_id):
            await self.close(code=4403)
            return

        self.shop_id = shop_id
        self.group_name = shop_group_name(shop_id)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        group_name = getattr(self, "group_name", None)
        if group_name:
            await self.channel_layer.group_discard(group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        action = content.get("action")
        if action == "ping":
            await self.send_json({"type": "pong"})
        elif action == "mark_read":
            channel = content.get("channel")
            psid = content.get("psid")
            if channel and psid:
                await self._mark_read(shop_id=self.shop_id, channel=channel, psid=psid)

    # ── group_send handlers (event["type"] dispatches to these) ──────────────

    async def chat_message(self, event):
        await self.send_json({"type": "chat_message", "payload": event["payload"]})

    async def conversation_update(self, event):
        await self.send_json({"type": "conversation_update", "payload": event["payload"]})

    # ── DB helpers ───────────────────────────────────────────────────────────

    @database_sync_to_async
    def _is_member(self, *, user_id, shop_id) -> bool:
        from shops.models import ShopMember
        return ShopMember.objects.filter(
            user_id=user_id,
            shop_id=shop_id,
            deleted_at__isnull=True,
            shop__deleted_at__isnull=True,
        ).exists()

    @database_sync_to_async
    def _mark_read(self, *, shop_id, channel, psid) -> None:
        from chat.selectors import mark_conversation_read
        mark_conversation_read(shop_id=shop_id, channel=channel, psid=psid)
