"""
Celery tasks for the messenger app.

Tasks:
  - process_inbound_message      — route a single webhook event through the
                                   greeting filter or AI engine
  - _flush_debounced_messages    — (internal) fire after debounce window to
                                   batch rapid-fire messages into one AI turn
  - embed_faq_entry              — generate pgvector embedding for a FAQEntry
  - sweep_old_messages           — soft-delete ChatMessages older than 30 days
"""
from __future__ import annotations

import json
import logging
import time

from celery import shared_task
from django.conf import settings
from django_redis import get_redis_connection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Debounce Redis key helpers
# ---------------------------------------------------------------------------

def _debounce_queue_key(shop_id: str, psid: str) -> str:
    return f"debounce:{shop_id}:{psid}"

def _debounce_meta_key(shop_id: str, psid: str) -> str:
    return f"debounce_meta:{shop_id}:{psid}"

def _debounce_lock_key(shop_id: str, psid: str) -> str:
    return f"debounce_lock:{shop_id}:{psid}"


# ---------------------------------------------------------------------------
# EPIC B-03 — Webhook fan-out / message routing
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    queue="messenger",
    name="chat.tasks.process_inbound_message",
)
def process_inbound_message(
    self,
    *,
    shop_id: str,
    page_id: str,
    psid: str,
    message_text: str | None,
    mid: str,
    timestamp: int,
    messaging_type: str = "message",
    postback_payload: str | None = None,
    comment_data: dict | None = None,
    page_access_token: str,
    channel: str = "FACEBOOK",
) -> None:
    """
    Route a single inbound event.

    `channel` is the ChannelChoices value the emitting adapter tagged the event
    with (FACEBOOK / WHATSAPP / WEB_WIDGET). It is threaded down into the AI turn
    so identity is stored against the correct value space — a WhatsApp waid must
    never be persisted as a FACEBOOK psid. Adapters that omit it default to
    FACEBOOK for backwards compatibility with legacy Messenger payloads.

    For WhatsApp, `psid` is the waid and `page_id` is the phone_number_id.

    messaging_type:
      "message"  → greeting filter → AI engine
      "postback" → deterministic postback handler
      "comment"  → comment auto-reply pipeline
    """
    from chat.services.bot_state import bot_state_is_human_active
    from shops.selectors import get_shop, get_shop_settings

    shop = get_shop(shop_id)
    if not shop:
        logger.error("process_inbound_message: shop %s not found", shop_id)
        return

    # Loop protection: drop messages where sender == page itself
    if psid == page_id:
        logger.debug("Loop protection: sender == page, dropping.")
        return

    # Human takeover silence — persist the inbound message so agents see it,
    # but do NOT invoke the bot. This is the same as the explicit HUMAN-only
    # mode path below; the difference is this is per-conversation, not global.
    if bot_state_is_human_active(page_id=page_id, psid=psid):
        logger.info("Human active for psid=%s — persisting inbound, bot silenced.", psid)
        if message_text:
            from chat.services.engine import _persist_message
            from chat.models import MessageDirection
            _persist_message(
                shop_id=shop_id, channel=channel, channel_identity=psid,
                direction=MessageDirection.INBOUND,
                text=message_text, timestamp=timestamp, page_id=page_id,
            )
        return

    settings_obj = get_shop_settings(shop_id)

    from chat.constants import bot_autoresponds

    # ── Comment auto-reply ──────────────────────────────────────────────────
    if messaging_type == "comment" and comment_data:
        if not bot_autoresponds():
            logger.info("HUMAN-only mode — skipping comment auto-reply for shop=%s", shop_id)
            return
        from chat.services.comment_autoreply import handle_comment_auto_reply
        handle_comment_auto_reply(
            shop_id=shop_id,
            page_id=page_id,
            psid=psid,
            post_id=comment_data.get("post_id", ""),
            comment_id=comment_data.get("comment_id", ""),
            page_access_token=page_access_token,
            product_ids=comment_data.get("product_ids", []),
        )
        return

    # ── Deterministic postbacks ─────────────────────────────────────────────
    if messaging_type == "postback" and postback_payload:
        if not bot_autoresponds():
            logger.info("HUMAN-only mode — skipping postback handler for shop=%s", shop_id)
            return
        _handle_postback(
            shop_id=shop_id,
            page_id=page_id,
            psid=psid,
            payload=postback_payload,
            page_access_token=page_access_token,
            settings_obj=settings_obj,
            channel=channel,
        )
        return

    # ── Regular message → greeting filter → AI ──────────────────────────────
    if not message_text:
        return

    # HUMAN-only mode: persist the inbound message (which bumps unread + pushes a
    # real-time event to agents) but do NOT invoke greeting/AI. Humans reply from
    # the inbox.
    if not bot_autoresponds():
        from chat.services.engine import _persist_message
        from chat.models import MessageDirection
        _persist_message(
            shop_id=shop_id, channel=channel, channel_identity=psid,
            direction=MessageDirection.INBOUND,
            text=message_text, timestamp=timestamp, page_id=page_id,
        )
        return

    from chat.services.greeting import is_greeting, greeting_reply_text
    from chat.channels.registry import get_adapter
    from chat.services.engine import _persist_message
    from chat.models import MessageDirection

    greeting_keywords = getattr(settings_obj, "messenger_greeting_keywords", None) if settings_obj else None
    if is_greeting(message_text=message_text, keywords=greeting_keywords):
        reply_text = greeting_reply_text(message_text=message_text)
        # Persist inbound greeting so it appears in the agent inbox history.
        _persist_message(
            shop_id=shop_id, channel=channel, channel_identity=psid,
            direction=MessageDirection.INBOUND,
            text=message_text, timestamp=timestamp, page_id=page_id,
        )
        get_adapter(channel).send_text(
            shop_id=shop_id, channel_identity=psid,
            text=reply_text, page_id=page_id,
        )
        # Persist the outbound greeting reply so agents see the full thread.
        out_ts = int(time.time() * 1000)
        _persist_message(
            shop_id=shop_id, channel=channel, channel_identity=psid,
            direction=MessageDirection.OUTBOUND,
            text=reply_text, timestamp=out_ts, page_id=page_id,
        )
        return

    # ── AI engine turn (debounced) ──────────────────────────────────────────
    # Instead of calling run_ai_turn immediately, queue the message into a
    # Redis debounce buffer. A single delayed task fires after the debounce
    # window closes and processes all accumulated messages as one AI turn.
    _debounce_and_schedule_ai_turn(
        shop_id=shop_id,
        page_id=page_id,
        psid=psid,
        message_text=message_text,
        timestamp=timestamp,
        channel=channel,
        settings_obj=settings_obj,
    )


# ---------------------------------------------------------------------------
# DEBOUNCE — batch rapid-fire messages into one AI turn
# ---------------------------------------------------------------------------

def _debounce_and_schedule_ai_turn(
    *,
    shop_id: str,
    page_id: str,
    psid: str,
    message_text: str,
    timestamp: int,
    channel: str,
    settings_obj,
) -> None:
    """
    Append message to a Redis debounce queue and (re)schedule a single
    delayed flush task.

    The debounce window is MESSAGE_DEBOUNCE_SECONDS (default 1.5s). If a
    new message arrives before the window expires, the TTL resets and the
    message is appended — no duplicate flush is scheduled.

    If Redis is unavailable, falls back to immediate single-message processing
    so messages are never dropped.
    """
    debounce_seconds = getattr(settings, "MESSAGE_DEBOUNCE_SECONDS", 1.5)
    queue_key = _debounce_queue_key(shop_id, psid)
    meta_key = _debounce_meta_key(shop_id, psid)
    lock_key = _debounce_lock_key(shop_id, psid)

    try:
        from django_redis import get_redis_connection
        r = get_redis_connection("default")
    except Exception as exc:
        # Redis unavailable — fail safe: process immediately as single-message
        logger.warning(
            "Debounce Redis unavailable (shop=%s psid=%s): %s — processing immediately.",
            shop_id, psid, exc,
        )
        _process_single_message_immediately(
            shop_id=shop_id, page_id=page_id, psid=psid,
            message_text=message_text, timestamp=timestamp,
            channel=channel, settings_obj=settings_obj,
        )
        return

    # If an AI turn is actively running, the lock_key is held by the currently
    # executing _flush_debounced_messages task. New messages will be appended
    # to the queue below but won't acquire the lock. When the active turn
    # finishes, it checks the queue and schedules a new flush for these messages.

    # Append message to the debounce queue (RPUSH preserves arrival order)
    entry = json.dumps({"text": message_text, "timestamp": timestamp})
    r.rpush(queue_key, entry)
    # Safety TTL so orphaned keys don't persist forever (300s to cover worst-case slow AI turn)
    r.expire(queue_key, 300)

    # Store metadata needed by the flush task (idempotent overwrite)
    r.hset(meta_key, mapping={
        "shop_id": shop_id,
        "page_id": page_id,
        "psid": psid,
        "channel": channel,
        "fallback_message": getattr(settings_obj, "messenger_fallback_message", "") or "" if settings_obj else "",
        "context_window_size": str(getattr(settings_obj, "messenger_context_window_size", 20) if settings_obj else 20),
    })
    r.expire(meta_key, 300)

    # Schedule the delayed flush task — use SETNX lock. We hold the lock for
    # up to 300 seconds (covering a worst-case slow AI turn) so that concurrent messages
    # queue up but do not spawn overlapping run_ai_turn executions.
    acquired = r.set(lock_key, "1", nx=True, ex=300)
    if acquired:
        _flush_debounced_messages.apply_async(
            kwargs={"shop_id": shop_id, "psid": psid},
            countdown=debounce_seconds,
            queue="messenger",
        )


def _process_single_message_immediately(
    *,
    shop_id: str,
    page_id: str,
    psid: str,
    message_text: str,
    timestamp: int,
    channel: str,
    settings_obj,
) -> None:
    """Fallback: process a single message without debouncing (Redis unavailable)."""
    from chat.services.engine import run_ai_turn
    from chat.channels.registry import get_adapter

    fallback = getattr(settings_obj, "messenger_fallback_message", None) if settings_obj else None
    ctx_size = getattr(settings_obj, "messenger_context_window_size", 20) if settings_obj else 20

    reply = run_ai_turn(
        shop_id=shop_id,
        channel=channel,
        channel_identity=psid,
        page_id=page_id,
        inbound_text=message_text,
        inbound_timestamp=timestamp,
        context_window_size=ctx_size,
        fallback_message=fallback,
    )
    get_adapter(channel).send_text(
        shop_id=shop_id, channel_identity=psid, text=reply, page_id=page_id,
    )


@shared_task(
    bind=True,
    max_retries=1,
    default_retry_delay=2,
    queue="messenger",
    name="chat.tasks._flush_debounced_messages",
)
def _flush_debounced_messages(self, *, shop_id: str, psid: str) -> None:
    """
    Fire after the debounce window closes. Pop all accumulated messages
    from the Redis queue and call run_ai_turn once with the batched texts.

    If new messages arrived since the task was scheduled (extending the
    debounce window), re-schedule self instead of processing — this
    implements the TTL-reset behaviour.
    """
    from django_redis import get_redis_connection
    from chat.services.engine import run_ai_turn
    from chat.channels.registry import get_adapter

    queue_key = _debounce_queue_key(shop_id, psid)
    meta_key = _debounce_meta_key(shop_id, psid)
    lock_key = _debounce_lock_key(shop_id, psid)
    debounce_seconds = getattr(settings, "MESSAGE_DEBOUNCE_SECONDS", 1.5)

    try:
        r = get_redis_connection("default")
    except Exception as exc:
        logger.error(
            "Flush debounced messages: Redis unavailable (shop=%s psid=%s): %s",
            shop_id, psid, exc,
        )
        return

    # Atomically pop ALL messages from the queue.
    # Use a pipeline: LRANGE + DELETE in one round-trip.
    pipe = r.pipeline()
    pipe.lrange(queue_key, 0, -1)
    pipe.delete(queue_key)
    results = pipe.execute()
    raw_messages = results[0]

    if not raw_messages:
        # Queue was empty — another flush already consumed it, or messages
        # were dropped. Clean up metadata and exit.
        r.delete(meta_key)
        return

    # Parse messages (arrival-order preserved by RPUSH)
    messages: list[tuple[str, int]] = []
    for raw in raw_messages:
        entry = json.loads(raw if isinstance(raw, str) else raw.decode())
        messages.append((entry["text"], entry["timestamp"]))

    # Load metadata
    meta_raw = r.hgetall(meta_key)
    r.delete(meta_key)

    if not meta_raw:
        logger.error(
            "Flush debounced messages: meta missing (shop=%s psid=%s) — dropping %d messages.",
            shop_id, psid, len(messages),
        )
        return

    # Decode metadata (redis returns bytes keys)
    meta = {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in meta_raw.items()
    }

    page_id = meta.get("page_id", "")
    channel = meta.get("channel", "FACEBOOK")
    fallback = meta.get("fallback_message") or None
    ctx_size = int(meta.get("context_window_size", "20"))

    try:
        if len(messages) == 1:
            # Single message — use the standard path (no batching overhead)
            text, ts = messages[0]
            reply = run_ai_turn(
                shop_id=shop_id,
                channel=channel,
                channel_identity=psid,
                page_id=page_id,
                inbound_text=text,
                inbound_timestamp=ts,
                context_window_size=ctx_size,
                fallback_message=fallback,
            )
        else:
            # Batched messages — pass all texts, persist each individually
            reply = run_ai_turn(
                shop_id=shop_id,
                channel=channel,
                channel_identity=psid,
                page_id=page_id,
                inbound_text=messages[0][0],  # required param, but inbound_texts takes precedence
                inbound_timestamp=messages[0][1],
                inbound_texts=messages,
                context_window_size=ctx_size,
                fallback_message=fallback,
            )

        get_adapter(channel).send_text(
            shop_id=shop_id, channel_identity=psid, text=reply, page_id=page_id,
        )
    finally:
        # Clear the scheduling lock after the turn finishes
        r.delete(lock_key)

    # Check if new messages arrived during the turn — if so, they are
    # sitting in the queue. Schedule a new flush for them.
    pending = r.llen(queue_key)
    if pending > 0:
        acquired = r.set(lock_key, "1", nx=True, ex=300)
        if acquired:
            _flush_debounced_messages.apply_async(
                kwargs={"shop_id": shop_id, "psid": psid},
                countdown=debounce_seconds,
                queue="messenger",
            )


def _handle_postback(
    *,
    shop_id: str,
    page_id: str,
    psid: str,
    payload: str,
    page_access_token: str,
    settings_obj,
    channel: str = "FACEBOOK",
) -> None:
    """Handle deterministic Persistent Menu / Ice Breaker postbacks."""
    from chat.channels.registry import get_adapter
    from chat.services.bot_state import bot_state_set_human_active

    adapter = get_adapter(channel)

    def _send(text: str) -> None:
        adapter.send_text(shop_id=shop_id, channel_identity=psid, text=text, page_id=page_id)

    if payload in ("ICE_SUPPORT", "HUMAN_SUPPORT"):
        ttl = getattr(settings_obj, "messenger_human_takeover_ttl_minutes", 30) if settings_obj else 30
        bot_state_set_human_active(page_id=page_id, psid=psid, ttl_minutes=ttl)
        _send("You've been connected with our support team. We'll be with you shortly! 👤")
    elif payload == "TRACK_ORDER":
        _send("Please share your order ID and I'll track it for you! 📦")
    elif payload == "FAQ_MENU":
        _send("Sure! What would you like to know? You can ask about returns, shipping, or any other topic.")
    elif payload == "ICE_BROWSE":
        _send("What are you looking for? Type a product name and I'll find the best options for you! 🛍")
    elif payload == "ICE_TRACK":
        _send("Share your order ID and I'll check the status for you! 📦")
    elif payload == "ICE_FAQ":
        _send("What would you like to know? Ask me about returns, shipping, or anything else!")
    else:
        logger.info("Unhandled postback payload: %s for shop=%s", payload, shop_id)


# ---------------------------------------------------------------------------
# Messenger page onboarding — subscribe page to webhooks + configure profile
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    queue="messenger",
    name="chat.tasks.onboard_messenger_page",
)
def onboard_messenger_page(self, *, shop_id: str, page_id: str) -> None:
    """
    Wire a freshly-connected Facebook Page for Messenger:
      1. Subscribe the app to the page's webhook events (else Meta delivers nothing).
      2. Configure the Messenger profile (Get Started, Ice Breakers, Persistent Menu)
         so the deterministic ICE_*/GET_STARTED postbacks can fire.

    The page access token is resolved from the SocialConnection rather than passed
    through the signal, so tokens never travel through the event payload.
    """
    from marketing.selectors import get_connection_by_page_id
    from chat.services.send_api import subscribe_page_to_webhooks, configure_messenger_profile
    from shops.selectors import get_shop

    conn = get_connection_by_page_id(page_id)
    if not conn or str(conn.shop_id) != str(shop_id):
        logger.warning("onboard_messenger_page: no connection for page=%s shop=%s", page_id, shop_id)
        return

    token = conn.access_token
    if not token:
        logger.warning("onboard_messenger_page: connection for page=%s has no token", page_id)
        return

    try:
        subscribe_page_to_webhooks(page_id=page_id, token=token)
    except Exception as exc:  # noqa: BLE001
        logger.error("onboard_messenger_page: subscribe failed page=%s: %s", page_id, exc)
        raise self.retry(exc=exc)

    # Profile config is best-effort — a failure here must not undo the subscription
    # (which is the part that unblocks inbound), so it is not retried.
    try:
        shop = get_shop(shop_id)
        shop_url = f"https://{shop.subdomain}.mohajon.store" if shop else "https://mohajon.store"
        configure_messenger_profile(token=token, shop_url=shop_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("onboard_messenger_page: profile config failed page=%s: %s", page_id, exc)


# ---------------------------------------------------------------------------
# Messenger user profile-name resolution
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=15,
    queue="messenger",
    name="chat.tasks.fetch_conversation_display_name",
)
def fetch_conversation_display_name(self, *, shop_id: str, page_id: str, psid: str) -> None:
    """
    Resolve a Messenger user's display name via the Graph User Profile API and
    store it on every FACEBOOK Conversation for this (shop, psid), then re-publish
    a conversation update so open inboxes swap the raw PSID for the real name live.

    Keyed on (shop_id, page_id, psid) — NOT a conversation id — so it is the common
    resolver for both the DM path and the comment-autoreply path, which share a PSID
    per page. If no conversation exists yet (comment-only user), the name is still
    fetched to prime the Redis guard; it lands on the conversation the moment one is
    created. Best-effort: any failure just leaves the PSID showing.
    """
    from chat.models import Conversation, ChannelChoices
    from chat.services.send_api import fetch_user_profile_data
    from marketing.selectors import get_connection_by_page_id

    conn = get_connection_by_page_id(page_id)
    if not conn or str(conn.shop_id) != str(shop_id) or not conn.access_token:
        logger.info("display-name: no usable connection for page=%s", page_id)
        return

    profile = fetch_user_profile_data(psid=psid, token=conn.access_token)
    if not profile:
        return

    name = profile.get("name")
    profile_pic = profile.get("profile_pic")

    from chat.services.realtime import publish_conversation_update
    conversations = Conversation.objects.filter(
        shop_id=shop_id, channel=ChannelChoices.FACEBOOK,
        channel_identity=psid, deleted_at__isnull=True,
    )
    for conversation in conversations:
        updated = False
        if name and conversation.metadata.get("name") != name:
            conversation.metadata["name"] = name
            updated = True
        if profile_pic and conversation.metadata.get("profile_pic") != profile_pic:
            conversation.metadata["profile_pic"] = profile_pic
            updated = True

        if updated:
            conversation.save(update_fields=["metadata"])
            publish_conversation_update(shop_id=shop_id, conversation=conversation)


# ---------------------------------------------------------------------------
# EPIC G-02 — RAG indexing (Imported from chat.tasks.rag)
# ---------------------------------------------------------------------------
from chat.tasks.rag import embed_faq_entry, embed_product_specs


# ---------------------------------------------------------------------------
# EPIC A-01 — Retention sweep (30-day soft-delete)
# ---------------------------------------------------------------------------

@shared_task(
    queue="default",
    name="chat.tasks.sweep_old_messages",
)
def sweep_old_messages() -> None:
    """
    Soft-delete ChatMessage records older than 30 days per the chat
    retention policy (global_business_rules_and_limits.md §4).
    """
    from django.utils import timezone
    from datetime import timedelta
    from chat.models import ChatMessage

    cutoff = timezone.now() - timedelta(days=30)
    updated = (
        ChatMessage.objects
        .filter(created_at__lt=cutoff, deleted_at__isnull=True)
        .update(deleted_at=timezone.now())
    )
    logger.info("sweep_old_messages: soft-deleted %d records older than 30 days.", updated)
