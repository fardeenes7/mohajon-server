"""
Messenger display-name resolution — the single, channel-actor-keyed seam that
turns a Facebook PSID into a human name for the inbox.

Both inbound paths funnel through here so a name is resolved once per PSID,
regardless of which surface the user first appeared on:
  - a direct message  (engine._persist_message on new conversation)
  - a post comment    (comment_autoreply, which DMs but never persists a turn)

Because a comment and a DM from the same user resolve to the SAME PSID on the
SAME page, the Graph lookup is deduplicated on (page_id, psid) via a Redis guard
with a long TTL — names change rarely and the profile call is rate-limited.
"""
from __future__ import annotations

import logging

from django_redis import get_redis_connection

logger = logging.getLogger(__name__)

# Names change rarely; a successful resolve is cached for a week so repeat
# comments/messages from the same user never re-hit the Graph profile endpoint.
_SYNC_TTL_SECONDS = 60 * 60 * 24 * 7


def _guard_key(page_id: str, psid: str) -> str:
    return f"msgr_name_synced:{page_id}:{psid}"


def queue_display_name_sync(*, shop_id: str, page_id: str, psid: str, force: bool = False) -> None:
    """
    Enqueue a best-effort display-name resolution for (page_id, psid), unless it
    was resolved recently. Safe to call on EVERY inbound event — the Redis guard
    makes repeat calls cheap no-ops. `force=True` bypasses the guard (e.g. manual
    backfill). Never raises: name resolution must not break message intake.
    """
    if not page_id or not psid:
        return
    try:
        if not force:
            r = get_redis_connection("default")
            # SET NX: only the first caller within the TTL window wins the fetch.
            won = r.set(_guard_key(page_id, psid), "1", nx=True, ex=_SYNC_TTL_SECONDS)
            if not won:
                return

        from chat.tasks import fetch_conversation_display_name
        fetch_conversation_display_name.delay(
            shop_id=str(shop_id), page_id=str(page_id), psid=str(psid),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("queue_display_name_sync failed page=%s psid=%s: %s", page_id, psid, exc)


def clear_sync_guard(*, page_id: str, psid: str) -> None:
    """Drop the dedup guard so the next event re-resolves the name."""
    try:
        get_redis_connection("default").delete(_guard_key(page_id, psid))
    except Exception:  # noqa: BLE001
        pass
