"""
Messenger message selectors (read-only queries).

All queries are tenant-scoped.  Selectors never perform writes.
"""
from __future__ import annotations

from django.db.models import Max

from chat.models import ChatMessage


def message_list_for_psid(
    *,
    shop_id: str,
    psid: str,
    limit: int = 20,
    before_timestamp: int | None = None,
) -> list[dict]:
    """
    Return the last `limit` messages for a PSID in chronological order
    (oldest first), suitable for populating the Redis context cache.

    Optionally filter to messages older than `before_timestamp` (Unix ms)
    for the AI's get_older_messages() tool.
    """
    qs = ChatMessage.objects.filter(
        shop_id=shop_id,
        psid=psid,
        deleted_at__isnull=True,
    )
    if before_timestamp is not None:
        qs = qs.filter(timestamp__lt=before_timestamp)

    records = qs.order_by("-timestamp")[:limit]

    return [
        {
            "role": "user" if m.direction == "INBOUND" else "assistant",
            "content": m.message_text or "(attachment)",
            "timestamp": m.timestamp,
        }
        for m in reversed(list(records))
    ]


def conversation_list_for_shop(*, shop_id: str, limit: int = 50) -> list[dict]:
    """
    Return latest conversations (distinct PSIDs) ordered by most recent
    activity.  Used to populate the Omnichannel Inbox list view.
    """
    qs = (
        ChatMessage.objects
        .filter(shop_id=shop_id, deleted_at__isnull=True)
        .values("psid", "page_id")
        .annotate(last_ts=Max("timestamp"))
        .order_by("-last_ts")[:limit]
    )
    return list(qs)
