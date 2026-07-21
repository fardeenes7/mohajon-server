"""
Real-time fan-out helper — the single chokepoint that pushes chat events to
agent dashboards over the Channels Redis layer.

Callable from BOTH the Celery worker (which persists bot messages) and DRF views
(agent-sent messages). Neither runs inside the ASGI process, so events are
written to the Redis channel layer; the ASGI consumers read them from Redis and
forward to connected WebSockets. This cross-process hop is exactly why the
Redis channel layer (not the in-memory one) is required.

Publishing must never break message persistence, so every failure is swallowed
with a log line.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _serialize_message(message) -> dict:
    return {
        "id": str(message.id),
        "conversation_id": str(message.conversation_id),
        "channel": message.conversation.channel,
        "channel_identity": message.conversation.channel_identity,
        "direction": message.direction,
        "text": message.text,
        "attachment_payload": message.attachment_payload,
        "timestamp": message.timestamp,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def _display_name(conversation) -> str:
    meta = conversation.metadata or {}
    return (
        meta.get("name")
        or meta.get("full_name")
        or meta.get("profile_name")
        or conversation.channel_identity
    )


def _bot_state(conversation) -> tuple[bool, bool]:
    """Return (human_active, bot_active) for this conversation. Best-effort; on any
    Redis error assume the bot is active (its default) and no human takeover."""
    try:
        from chat.services.bot_state import bot_state_is_human_active
        from chat.constants import bot_autoresponds

        page_id = (conversation.metadata or {}).get("page_id") or ""
        human = bot_state_is_human_active(page_id=page_id, psid=conversation.channel_identity)
        return human, (bot_autoresponds() and not human)
    except Exception:  # noqa: BLE001
        return False, True


def _serialize_conversation(conversation) -> dict:
    # Shape mirrors ConversationListSerializer so the frontend can upsert a live
    # conversation_update straight into the same list it rendered from the API.
    human_active, bot_active = _bot_state(conversation)
    return {
        "id": str(conversation.id),
        "channel": conversation.channel,
        "channel_identity": conversation.channel_identity,
        "metadata": conversation.metadata or {},
        "page_id": conversation.metadata.get("page_id"),
        "display_name": _display_name(conversation),
        "has_unread": conversation.has_unread,
        "unread_count": conversation.unread_count,
        "human_active": human_active,
        "bot_active": bot_active,
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
    }


def publish_chat_event(*, shop_id: str, event_type: str, payload: dict) -> None:
    """Low-level fan-out to a shop's agent group. `event_type` must match a
    handler method name on AgentInboxConsumer (chat_message / conversation_update)."""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        from chat.consumers import shop_group_name

        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(
            shop_group_name(str(shop_id)),
            {"type": event_type, "payload": payload},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("publish_chat_event failed shop=%s type=%s: %s", shop_id, event_type, exc)


def publish_new_message(*, shop_id: str, message) -> None:
    publish_chat_event(shop_id=shop_id, event_type="chat_message", payload=_serialize_message(message))


def publish_conversation_update(*, shop_id: str, conversation) -> None:
    publish_chat_event(
        shop_id=shop_id, event_type="conversation_update",
        payload=_serialize_conversation(conversation),
    )
