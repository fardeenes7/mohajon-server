"""
Celery tasks for the messenger app.

Tasks:
  - process_inbound_message  — route a single webhook event through the
                               greeting filter or AI engine
  - embed_faq_entry          — generate pgvector embedding for a FAQEntry
  - sweep_old_messages       — soft-delete ChatMessages older than 30 days
"""
from __future__ import annotations

import logging
import time

from celery import shared_task

logger = logging.getLogger(__name__)


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
) -> None:
    """
    Route a single inbound Messenger event.

    messaging_type:
      "message"  → greeting filter → AI engine
      "postback" → deterministic postback handler
      "comment"  → comment auto-reply pipeline
    """
    from chat.services.bot_state import bot_state_is_human_active
    from shops.models import Shop, ShopSettings

    try:
        shop = Shop.objects.get(id=shop_id, deleted_at__isnull=True)
    except Shop.DoesNotExist:
        logger.error("process_inbound_message: shop %s not found", shop_id)
        return

    # Loop protection: drop messages where sender == page itself
    if psid == page_id:
        logger.debug("Loop protection: sender == page, dropping.")
        return

    # Human takeover silence
    if bot_state_is_human_active(page_id=page_id, psid=psid):
        logger.info("Human active for psid=%s — bot is silenced.", psid)
        return

    try:
        settings_obj = shop.settings
    except ShopSettings.DoesNotExist:
        settings_obj = None

    # ── Comment auto-reply ──────────────────────────────────────────────────
    if messaging_type == "comment" and comment_data:
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
        _handle_postback(
            shop_id=shop_id,
            page_id=page_id,
            psid=psid,
            payload=postback_payload,
            page_access_token=page_access_token,
            settings_obj=settings_obj,
        )
        return

    # ── Regular message → greeting filter → AI ──────────────────────────────
    if not message_text:
        return

    from chat.services.greeting import is_greeting, greeting_reply_text
    from chat.services.send_api import send_text

    greeting_keywords = getattr(settings_obj, "messenger_greeting_keywords", None) if settings_obj else None
    if is_greeting(message_text=message_text, keywords=greeting_keywords):
        send_text(psid=psid, text=greeting_reply_text(), token=page_access_token)
        return

    # AI engine turn
    from chat.services.engine import run_ai_turn
    fallback = getattr(settings_obj, "messenger_fallback_message", None) if settings_obj else None
    ctx_size = getattr(settings_obj, "messenger_context_window_size", 20) if settings_obj else 20

    reply = run_ai_turn(
        shop_id=shop_id,
        channel=page_id,  # Using page_id as channel for Meta
        channel_identity=psid,
        inbound_text=message_text,
        inbound_timestamp=timestamp,
        context_window_size=ctx_size,
        fallback_message=fallback,
    )
    send_text(psid=psid, text=reply, token=page_access_token)


def _handle_postback(
    *,
    shop_id: str,
    page_id: str,
    psid: str,
    payload: str,
    page_access_token: str,
    settings_obj,
) -> None:
    """Handle deterministic Persistent Menu / Ice Breaker postbacks."""
    from chat.services.send_api import send_text
    from chat.services.bot_state import bot_state_set_human_active

    if payload in ("ICE_SUPPORT", "HUMAN_SUPPORT"):
        ttl = getattr(settings_obj, "messenger_human_takeover_ttl_minutes", 30) if settings_obj else 30
        bot_state_set_human_active(page_id=page_id, psid=psid, ttl_minutes=ttl)
        send_text(
            psid=psid,
            text="You've been connected with our support team. We'll be with you shortly! 👤",
            token=page_access_token,
        )
    elif payload == "TRACK_ORDER":
        send_text(psid=psid, text="Please share your order ID and I'll track it for you! 📦", token=page_access_token)
    elif payload == "FAQ_MENU":
        send_text(psid=psid, text="Sure! What would you like to know? You can ask about returns, shipping, or any other topic.", token=page_access_token)
    elif payload == "ICE_BROWSE":
        send_text(psid=psid, text="What are you looking for? Type a product name and I'll find the best options for you! 🛍", token=page_access_token)
    elif payload == "ICE_TRACK":
        send_text(psid=psid, text="Share your order ID and I'll check the status for you! 📦", token=page_access_token)
    elif payload == "ICE_FAQ":
        send_text(psid=psid, text="What would you like to know? Ask me about returns, shipping, or anything else!", token=page_access_token)
    else:
        logger.info("Unhandled postback payload: %s for shop=%s", payload, shop_id)


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
