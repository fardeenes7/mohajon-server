from __future__ import annotations

from typing import List, Dict, Any
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from chat.models import Conversation, ChatMessage

def conversation_count_for_shop(shop_id: str) -> int:
    return Conversation.objects.filter(shop_id=shop_id).count()

def unread_inbox_count_for_shop(shop_id: str) -> int:
    return Conversation.objects.filter(
        shop_id=shop_id, has_unread=True, deleted_at__isnull=True
    ).count()

def conversation_list_for_shop(*, shop_id: str):
    """
    Conversations for the agent inbox, newest activity first, each annotated
    with a snippet of its most recent message so the sidebar can render a
    preview without an N+1 query per row.
    """
    latest = (
        ChatMessage.objects.filter(
            conversation_id=OuterRef("pk"), deleted_at__isnull=True
        )
        .order_by("-timestamp")
    )
    return (
        Conversation.objects.filter(shop_id=shop_id, deleted_at__isnull=True)
        .annotate(
            last_message_text=Subquery(latest.values("text")[:1]),
            last_message_direction=Subquery(latest.values("direction")[:1]),
            last_message_at=Subquery(latest.values("timestamp")[:1]),
        )
        .order_by("-updated_at")
    )

def inbox_message_history(*, shop_id: str, psid: str, channel: str, limit: int = 50, before_timestamp: int | None = None) -> List[Dict[str, Any]]:
    """
    Message history for the agent inbox UI, oldest-first, in the SAME shape the
    realtime layer pushes (see chat.services.realtime._serialize_message) so the
    frontend appends live events to the initial fetch without reshaping.

    Distinct from message_list_for_psid, which returns the role/content shape the
    AI engine consumes — do not merge the two.
    """
    qs = ChatMessage.objects.filter(
        shop_id=shop_id,
        conversation__channel=channel,
        conversation__channel_identity=psid,
        deleted_at__isnull=True,
    )
    if before_timestamp is not None:
        qs = qs.filter(timestamp__lt=before_timestamp)

    messages = qs.select_related("conversation").order_by("-timestamp")[:limit]

    return [
        {
            "id": str(msg.id),
            "conversation_id": str(msg.conversation_id),
            "channel": msg.conversation.channel,
            "channel_identity": msg.conversation.channel_identity,
            "direction": msg.direction,
            "text": msg.text,
            "attachment_payload": msg.attachment_payload,
            "timestamp": msg.timestamp,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        }
        for msg in reversed(messages)
    ]

def mark_conversation_read(*, shop_id: str, channel: str, psid: str) -> None:
    """Reset unread state when an agent opens a conversation."""
    Conversation.objects.filter(
        shop_id=shop_id,
        channel=channel,
        channel_identity=psid,
        deleted_at__isnull=True,
    ).update(has_unread=False, unread_count=0, last_read_at=timezone.now())

def message_list_for_psid(*, shop_id: str, psid: str, channel: str, limit: int = 20, before_timestamp: int | None = None) -> List[Dict[str, Any]]:
    # (channel, channel_identity) is the conversation key — filtering on
    # channel_identity alone would merge histories across channels if a PSID and
    # a waid ever coincide.
    qs = ChatMessage.objects.filter(
        shop_id=shop_id,
        conversation__channel=channel,
        conversation__channel_identity=psid,
        deleted_at__isnull=True
    ).exclude(direction="SYSTEM")  # SYSTEM markers are UI-only; never feed to the AI
    if before_timestamp is not None:
        qs = qs.filter(timestamp__lt=before_timestamp)

    messages = qs.order_by("-timestamp")[:limit]

    # Needs to return a list of dicts with role and content for the engine
    results = []
    for msg in reversed(messages):
        role = "assistant" if msg.direction == "OUTBOUND" else "user"
        results.append({
            "role": role,
            "content": msg.text or "",
            "timestamp": msg.timestamp,
        })
    return results

