"""
ConversationBotState — Redis-backed per-conversation state.

Redis hash keyed by  bot_state:{page_id}:{psid}
TTL: 24 hours (order_draft), 30 min (human_active flag — per ShopSettings)

Structure of the hash:
  human_active            -> "1" | "0"
  human_active_expires_at -> Unix timestamp (str)
  order_draft             -> JSON string of the partially assembled order
  last_active_at          -> Unix timestamp (str)
"""
from __future__ import annotations

import json
import time
from typing import Any

from django_redis import get_redis_connection


def _key(page_id: str, psid: str) -> str:
    return f"bot_state:{page_id}:{psid}"


def _ctx_key(page_id: str, psid: str) -> str:
    return f"messenger_ctx:{page_id}:{psid}"


# ---------------------------------------------------------------------------
# Human Takeover  (EPIC F-02 backend)
# ---------------------------------------------------------------------------

def bot_state_set_human_active(
    *,
    page_id: str,
    psid: str,
    ttl_minutes: int = 30,
) -> None:
    """Silence the bot for this PSID.  Used by agent 'Take Over' action."""
    r = get_redis_connection("default")
    expires_at = int(time.time()) + ttl_minutes * 60
    key = _key(page_id, psid)
    r.hset(key, mapping={
        "human_active": "1",
        "human_active_expires_at": str(expires_at),
        "last_active_at": str(int(time.time())),
    })
    # Overall key TTL is keyed to the draft (24h); human flag has its own expiry field.
    r.expire(key, 60 * 60 * 24)


def bot_state_clear_human_active(*, page_id: str, psid: str) -> None:
    """Explicitly hand conversation back to the bot."""
    r = get_redis_connection("default")
    key = _key(page_id, psid)
    r.hset(key, mapping={
        "human_active": "0",
        "human_active_expires_at": "0",
        "last_active_at": str(int(time.time())),
    })


def bot_state_is_human_active(*, page_id: str, psid: str) -> bool:
    """
    Returns True if a human agent currently owns this conversation.
    Checks both the flag AND the expiry timestamp — the Redis key TTL is
    the ultimate authority, but we validate the expiry field to catch
    cases where the key has been refreshed by an unrelated write.
    """
    r = get_redis_connection("default")
    key = _key(page_id, psid)
    data = r.hgetall(key)
    if not data:
        return False
    human_active = data.get(b"human_active", b"0").decode() == "1"
    if not human_active:
        return False
    expires_at_raw = data.get(b"human_active_expires_at", b"0")
    expires_at = int(expires_at_raw.decode() or 0)
    if expires_at and time.time() > expires_at:
        # Flag has logically expired even if the key still exists.
        bot_state_clear_human_active(page_id=page_id, psid=psid)
        return False
    return True


# ---------------------------------------------------------------------------
# Order Draft  (EPIC C — multi-turn checkout)
# ---------------------------------------------------------------------------

def bot_state_set_order_draft(
    *,
    page_id: str,
    psid: str,
    draft: dict[str, Any],
    ttl_hours: int = 24,
) -> None:
    """Persist a partial order draft.  Survives unrelated AI turns for 24h."""
    r = get_redis_connection("default")
    key = _key(page_id, psid)
    r.hset(key, mapping={
        "order_draft": json.dumps(draft),
        "last_active_at": str(int(time.time())),
    })
    r.expire(key, ttl_hours * 3600)


def bot_state_get_order_draft(*, page_id: str, psid: str) -> dict[str, Any] | None:
    r = get_redis_connection("default")
    raw = r.hget(_key(page_id, psid), "order_draft")
    if not raw:
        return None
    return json.loads(raw.decode())


def bot_state_clear_order_draft(*, page_id: str, psid: str) -> None:
    r = get_redis_connection("default")
    r.hdel(_key(page_id, psid), "order_draft")


# ---------------------------------------------------------------------------
# Context Cache  (EPIC C-02)
# ---------------------------------------------------------------------------

CTX_TTL_SECONDS = 7200  # 2 hours


def ctx_cache_append(
    *,
    page_id: str,
    psid: str,
    role: str,
    content: str,
    max_size: int = 20,
) -> None:
    """LPUSH a message entry; LTRIM to keep only the last max_size entries."""
    r = get_redis_connection("default")
    key = _ctx_key(page_id, psid)
    entry = json.dumps({"role": role, "content": content, "timestamp": int(time.time())})
    r.lpush(key, entry)
    r.ltrim(key, 0, max_size - 1)
    r.expire(key, CTX_TTL_SECONDS)


def ctx_cache_get(*, page_id: str, psid: str) -> list[dict] | None:
    """
    Returns messages in chronological order (oldest first) or None if
    the cache key is missing (cold conversation — caller must Postgres-fallback).
    """
    r = get_redis_connection("default")
    key = _ctx_key(page_id, psid)
    if not r.exists(key):
        return None
    raw_messages = r.lrange(key, 0, -1)
    # LPUSH means index 0 = newest; reverse to get chronological order.
    return [json.loads(m.decode()) for m in reversed(raw_messages)]


def ctx_cache_populate(
    *,
    page_id: str,
    psid: str,
    messages: list[dict],
    max_size: int = 20,
) -> None:
    """Bulk-populate the context cache from Postgres fallback."""
    r = get_redis_connection("default")
    key = _ctx_key(page_id, psid)
    r.delete(key)
    # messages should be chronological (oldest first); LPUSH newest last.
    for msg in messages[-max_size:]:
        entry = json.dumps(msg)
        r.lpush(key, entry)
    r.expire(key, CTX_TTL_SECONDS)
