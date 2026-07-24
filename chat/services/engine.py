"""
OpenAI Function-Calling Engine (EPIC C).

Responsibilities:
  1. Load the conversation context (Redis hot path → Postgres fallback).
  2. Build the system prompt with shop identity + policies.
  3. Run the OpenAI tool-call loop (max 5 function calls per turn) via AIGateway.
  4. Persist the new messages to Redis cache + ChatMessage table.
  5. Delegate credit deduction + AIUsageLog audit to AIGateway.log_accumulated_usage().
  6. Handle OpenAI failures with retry → fallback message → DLQ alert.

Note: This module contains NO OpenAI client initialisation, model resolution, or
credit math.  All of that is owned by core.services.ai_gateway.AIGateway.

Scope note: online gateway finalization is v0.8; COD path is live from v0.6.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone

from ai.models import AIModelUsage
from ai.services.ai_credits import (
    has_sufficient_ai_credits,
    InsufficientCreditsError,
    reconcile_ai_credits,
    release_ai_credits,
    reserve_ai_credits,
)
from ai.services.ai_gateway import AIGateway
from chat.models import ChatMessage, MessageDirection
from chat.selectors import message_list_for_psid
from chat.services.bot_state import ctx_cache_append, ctx_cache_get, ctx_cache_populate
from chat.services.tools import TOOL_SCHEMAS, execute_tool

logger = logging.getLogger(__name__)

# Max consecutive function calls per AI turn (global_business_rules_and_limits.md §5)
_MAX_TOOL_CALLS = 5


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def _build_system_prompt(*, shop_id: str) -> str:
    from shops.selectors import get_shop

    shop = get_shop(shop_id)
    if shop:
        shop_name = shop.name
        currency = shop.base_currency
    else:
        shop_name = "Our Shop"
        currency = "BDT"

    now_str = timezone.now().strftime("%A, %d %B %Y, %I:%M %p UTC")

    return (
        f"You are **{shop_name}**'s AI shopping assistant on Messenger — think of yourself as a "
        f"warm, switched-on salesperson at the shop's counter, not a robot.\n"
        f"Current date and time: {now_str}.\n"
        f"All prices are in {currency} (৳ for BDT).\n"
        "\n"
        "## Who you are\n"
        "- You genuinely want to help the customer find the right thing and feel taken care of.\n"
        "- You know the shop's products, prices, offers, and policies (use your tools to look them up — "
        "never guess).\n"
        "- You are honest: if something is out of stock, unavailable, or you don't know, you say so kindly "
        "and offer the next best step.\n"
        "\n"
        "## Language — match the customer, always\n"
        "- Reply in the SAME language the customer used, turn by turn:\n"
        "  • Bangla message → reply in natural, everyday Bangla (not stiff/literary Bangla).\n"
        "  • English message → reply in English.\n"
        "  • Banglish (Bangla written in English letters, e.g. \"vai ei shirt ta ache?\") → reply in "
        "Banglish the same way. Do NOT switch them to Bangla script unless they wrote in Bangla script.\n"
        "- If they mix languages, mirror the mix. When unsure, lean Bangla — most customers are "
        "Bangladeshi.\n"
        "- Keep numbers, sizes, and prices clear regardless of language.\n"
        "\n"
        "## Tone — casually formal, sounds like a real person\n"
        "- Warm, respectful, and human — like a friendly shopkeeper, not a call-center script.\n"
        "- In Bangla use the polite 'আপনি' form (never 'তুই/তুমি'). Be friendly but never overly casual "
        "or slangy.\n"
        "- Short, natural sentences. A little warmth and the occasional emoji (🙂🛍️👍) is good; don't "
        "overdo it.\n"
        "- Don't sound scripted or repeat the same canned line every message. Vary naturally.\n"
        "\n"
        "## Islamic greeting etiquette (important)\n"
        "- If the customer greets YOU with a salam first (\"assalamu alaikum\", \"আসসালামু আলাইকুম\", "
        "\"slm\", \"salam\", etc.), you MUST reply beginning with **\"Walaikum assalam\"** "
        "(Bangla: \"ওয়ালাইকুম আসসালাম\") before anything else.\n"
        "- When YOU start a greeting or welcome (first contact, or greeting someone who did not salam "
        "first), begin with **\"Assalamu alaikum\"** (Bangla: \"আসসালামু আলাইকুম\"), then continue "
        "warmly — e.g. \"Assalamu alaikum! Welcome to {shop} 🙂 how can I help?\".\n"
        "- Match the greeting to the reply language (Bangla script for Bangla, Latin for English/Banglish).\n"
        "- Do not add a salam to the middle of an ongoing conversation — only at genuine greetings.\n"
        "\n"
        "## How you work\n"
        "- Call a tool whenever you are unsure — for product availability, price, stock, order status, or "
        "policies — instead of making something up.\n"
        "- If a question is about shop policies (returns, shipping, exchange, etc.), call `search_faq` "
        "BEFORE answering from your own knowledge.\n"
        "- If `search_faq` returns nothing above the threshold, tell the customer you don't have that "
        "detail and offer to connect them with a human agent.\n"
        "- NEVER call `confirm_order` directly. Always call `prepare_order_draft` first, show the customer a "
        "clear summary (items, quantities, price, delivery), and wait for their explicit confirmation "
        "before placing the order.\n"
        "- If `confidence < 0.65`, ask one short clarifying question before calling any mutating tool.\n"
        "- Never expose raw error strings, stack traces, internal IDs, or system details to the customer. "
        "If something breaks, apologize briefly and offer to have the team follow up.\n"
        "- Keep the customer moving toward a helpful outcome: answer, suggest, and gently guide toward a "
        "purchase or resolution without being pushy.\n"
    ).replace("{shop}", shop_name)


# ---------------------------------------------------------------------------
# Message persistence
# ---------------------------------------------------------------------------

def _persist_message(
    *,
    shop_id: str,
    channel: str,
    channel_identity: str,
    direction: str,
    text: str | None,
    timestamp: int,
    attachment_payload: dict | None = None,
    page_id: str | None = None,
) -> None:
    from chat.models import Conversation, ChatMessage
    
    conversation, created = Conversation.objects.get_or_create(
        shop_id=shop_id,
        tenant_id=shop_id,
        channel=channel,
        channel_identity=channel_identity,
    )

    if page_id and conversation.metadata.get("page_id") != page_id:
        conversation.metadata["page_id"] = page_id
        conversation.save(update_fields=["metadata"])

    # Layer 2 identity seam: resolve/create the ChannelActor and attach it to the
    # conversation. This is the ONLY entry point into the identity graph — never
    # import identity models here. Resilience is mandatory: identity resolution
    # must NEVER break message persistence, so any failure is logged and swallowed.
    if conversation.actor_id is None:
        try:
            from identity.services.actors import get_or_create_channel_actor

            actor = get_or_create_channel_actor(
                shop_id=shop_id,
                channel=channel,
                channel_identity=channel_identity,
            )
            conversation.actor = actor
            conversation.save(update_fields=["actor"])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ChannelActor resolution failed for shop=%s channel=%s identity=%s: %s",
                shop_id, channel, channel_identity, exc,
            )

    message = ChatMessage.objects.create(
        shop_id=shop_id,
        tenant_id=shop_id,
        conversation=conversation,
        direction=direction,
        text=text,
        attachment_payload=attachment_payload,
        timestamp=timestamp,
    )

    # Bump updated_at on every message so the inbox sorts by real last-activity
    # and the sidebar shows the latest time. A queryset .update() bypasses the
    # auto_now field, so we must set it explicitly. Only inbound messages create
    # unread work for agents; use an atomic F() update to avoid a read-modify-write
    # race between concurrent turns.
    from django.db.models import F
    from django.utils import timezone
    update_fields = {"updated_at": timezone.now()}
    if direction == MessageDirection.INBOUND:
        update_fields["has_unread"] = True
        update_fields["unread_count"] = F("unread_count") + 1
    Conversation.objects.filter(pk=conversation.pk).update(**update_fields)
    conversation.refresh_from_db(fields=["has_unread", "unread_count", "updated_at"])

    # Push the new message + conversation summary to the shop's agent dashboards.
    from chat.services.realtime import publish_new_message, publish_conversation_update
    publish_new_message(shop_id=shop_id, message=message)
    publish_conversation_update(shop_id=shop_id, conversation=conversation)

    # Resolve the Messenger user's profile name off the hot path so the inbox
    # shows a real name instead of the raw PSID. Deduplicated per (page, psid) in
    # the profile service, so calling on every FB message is a cheap no-op after
    # the first. Best-effort: the conversation is already usable without it.
    if channel == "FACEBOOK" and page_id:
        from chat.services.profile import queue_display_name_sync
        queue_display_name_sync(shop_id=str(shop_id), page_id=page_id, psid=channel_identity)

    return message


def record_system_event(
    *,
    shop_id: str,
    channel: str,
    channel_identity: str,
    event: str,
    text: str,
    page_id: str | None = None,
) -> None:
    """
    Record a non-conversational timeline event (human takeover, handback, bot
    auto-paused, …) as a SYSTEM message and push it live to agent dashboards.

    Persisted so the marker survives a refresh; `event` is a stable machine key
    (e.g. "human_takeover") the UI can use to pick an icon, `text` is the human
    label. SYSTEM messages are never delivered to the customer.
    """
    from chat.models import Conversation, ChatMessage, MessageDirection
    import time

    conversation, _ = Conversation.objects.get_or_create(
        shop_id=shop_id, tenant_id=shop_id,
        channel=channel, channel_identity=channel_identity,
    )
    if page_id and conversation.metadata.get("page_id") != page_id:
        conversation.metadata["page_id"] = page_id
        conversation.save(update_fields=["metadata"])

    message = ChatMessage.objects.create(
        shop_id=shop_id, tenant_id=shop_id, conversation=conversation,
        direction=MessageDirection.SYSTEM,
        text=text, attachment_payload={"event": event},
        timestamp=int(time.time() * 1000),
    )

    from chat.services.realtime import publish_new_message
    publish_new_message(shop_id=shop_id, message=message)


# ---------------------------------------------------------------------------
# AI failure classification → conversation marker
# ---------------------------------------------------------------------------

def _classify_ai_failure(exc: Exception) -> tuple[str, str]:
    """
    Map an AI-turn failure to a (machine key, agent-facing label) pair for a
    SYSTEM timeline marker. The key is a stable string the inbox UI uses to pick
    an icon; the label is the human text shown on the marker. These markers are
    agent-facing only — the customer still receives the friendly fallback reply.
    """
    from openai import (
        RateLimitError,
        PermissionDeniedError,
        APIConnectionError,
        BadRequestError,
    )

    if isinstance(exc, RateLimitError):
        return "ai_rate_limited", "Bot skipped — AI is rate-limited right now. Please retry in a moment."
    if isinstance(exc, PermissionDeniedError):
        return "ai_model_unavailable", "Bot skipped — the AI model is currently unavailable."

    detail = f"{getattr(exc, 'code', '') or ''} {exc}".lower()
    if isinstance(exc, BadRequestError) and (
        "context_length" in detail or "maximum context" in detail or "too many tokens" in detail
    ):
        return "ai_context_overflow", "Bot skipped — the conversation is too long for the AI to process."
    if isinstance(exc, APIConnectionError):
        return "ai_connection_error", "Bot skipped — couldn't reach the AI service."
    return "ai_error", "Bot skipped — the AI ran into an unexpected problem."


# ---------------------------------------------------------------------------
# Main engine entry point
# ---------------------------------------------------------------------------

def run_ai_turn(
    *,
    shop_id: str,
    channel: str,
    channel_identity: str,
    inbound_text: str,
    inbound_timestamp: int,
    page_id: str | None = None,
    context_window_size: int = 20,
    fallback_message: str | None = None,
    inbound_texts: list[tuple[str, int]] | None = None,
) -> str:
    """
    Execute one AI turn:
      1. Persist the inbound message(s).
      2. Load context (Redis → Postgres fallback).
      3. Reserve credits (optimistic Redis-based limiter).
      4. Run OpenAI tool-call loop (max 5 calls).
      5. Deduct credits + reconcile reservation.
      6. Persist outbound reply.
      7. Return the final text to send to the customer.

    On any failure, returns the fallback_message string.

    When ``inbound_texts`` is provided (list of (text, timestamp) tuples from
    the debounce flush), each message is persisted as a separate ChatMessage
    row so the agent inbox shows the exact messages the customer sent, but the
    AI sees them concatenated into one user turn. Token/credit accounting
    treats this as a single AI turn.
    """
    import time

    # 1. Persist inbound message(s)
    # When multiple batched messages arrive via debounce, persist each
    # individually so the agent inbox timeline stays correct.
    if inbound_texts:
        for text, ts in inbound_texts:
            _persist_message(
                shop_id=shop_id, channel=channel, channel_identity=channel_identity,
                direction=MessageDirection.INBOUND,
                text=text, timestamp=ts, page_id=page_id,
            )
        # The combined text sent to the AI is a newline-joined concatenation
        combined_inbound_text = "\n".join(text for text, _ in inbound_texts)
    else:
        _persist_message(
            shop_id=shop_id, channel=channel, channel_identity=channel_identity,
            direction=MessageDirection.INBOUND,
            text=inbound_text, timestamp=inbound_timestamp,
            page_id=page_id,
        )
        combined_inbound_text = inbound_text

    # 2. Load context
    ctx_messages = ctx_cache_get(page_id=page_id or channel, psid=channel_identity)
    if ctx_messages is None:
        # Cold path — load from Postgres and warm the cache
        db_messages = message_list_for_psid(
            shop_id=shop_id, psid=channel_identity, channel=channel, limit=context_window_size
        )
        ctx_cache_populate(page_id=page_id or channel, psid=channel_identity, messages=db_messages, max_size=context_window_size)
        ctx_messages = db_messages

    # Build messages list for OpenAI
    openai_messages: list[dict[str, Any]] = [
        {"role": "system", "content": _build_system_prompt(shop_id=shop_id)}
    ]
    for m in ctx_messages[-context_window_size:]:
        openai_messages.append({"role": m["role"], "content": m["content"]})
    openai_messages.append({"role": "user", "content": combined_inbound_text})

    _DEFAULT_FALLBACK = "I'm having a little trouble right now. Our team will reach out shortly! 🙏"

    def _persist_fallback() -> str:
        """Persist the fallback reply so it appears in the agent inbox, then return it."""
        reply = fallback_message or _DEFAULT_FALLBACK
        _persist_message(
            shop_id=shop_id, channel=channel, channel_identity=channel_identity,
            direction=MessageDirection.OUTBOUND,
            text=reply, timestamp=int(time.time() * 1000), page_id=page_id,
        )
        return reply

    # 3. Credit pre-check + reservation
    if not has_sufficient_ai_credits(shop_id=shop_id):
        _handle_credit_exhaustion(shop_id=shop_id, page_id=page_id or channel, psid=channel_identity)
        record_system_event(
            shop_id=shop_id, channel=channel, channel_identity=channel_identity,
            event="bot_paused_credits",
            text="Bot paused — AI credits exhausted. A human should take over.",
            page_id=page_id,
        )
        return _persist_fallback()

    # Reserve credits upfront to prevent concurrent turns from overspending.
    # The reservation is released in the finally block below regardless of
    # whether the turn succeeds or crashes.
    reserved_credits = Decimal("0")
    try:
        reserved_credits = reserve_ai_credits(shop_id=shop_id)
    except InsufficientCreditsError:
        _handle_credit_exhaustion(shop_id=shop_id, page_id=page_id or channel, psid=channel_identity)
        record_system_event(
            shop_id=shop_id, channel=channel, channel_identity=channel_identity,
            event="bot_paused_credits",
            text="Bot paused — AI credits exhausted. A human should take over.",
            page_id=page_id,
        )
        return _persist_fallback()

    # 4. OpenAI tool-call loop — all AI concerns routed through AIGateway
    gateway = AIGateway(shop_id=shop_id, reference_id=channel_identity)
    total_input_tokens = 0
    total_output_tokens = 0
    tool_call_depth = 0
    final_reply = fallback_message or "I'm having a little trouble right now. Our team will reach out shortly! 🙏"
    turn_succeeded = False

    try:
        while True:
            response = gateway.call_chat_with_tools(
                messages=openai_messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
            )
            usage = response.usage
            if usage:
                total_input_tokens += usage.prompt_tokens
                total_output_tokens += usage.completion_tokens

            choice = response.choices[0]
            msg = choice.message

            # Append assistant message to context
            openai_messages.append(msg.model_dump(exclude_unset=True))

            if choice.finish_reason == "tool_calls" and msg.tool_calls:
                if tool_call_depth >= _MAX_TOOL_CALLS:
                    logger.warning(
                        "AI tool-call depth limit reached for shop=%s identity=%s", shop_id, channel_identity
                    )
                    _push_dlq_alert(shop_id=shop_id, reason="max_tool_calls_exceeded")
                    record_system_event(
                        shop_id=shop_id, channel=channel, channel_identity=channel_identity,
                        event="ai_tool_limit",
                        text="Bot skipped — the AI needed too many lookups to answer this one.",
                        page_id=page_id,
                    )
                    break

                for tool_call in msg.tool_calls:
                    tool_call_depth += 1
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments or "{}")
                    result = execute_tool(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        shop_id=shop_id,
                        psid=channel_identity,
                        page_id=page_id or channel,
                        channel=channel,
                    )
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result),
                    })
            else:
                final_reply = (msg.content or "").strip()
                break

        turn_succeeded = True

    except Exception as exc:
        logger.error("AI engine error shop=%s identity=%s: %s", shop_id, channel_identity, exc)
        _push_dlq_alert(shop_id=shop_id, reason=str(exc))
        event_key, event_label = _classify_ai_failure(exc)
        record_system_event(
            shop_id=shop_id, channel=channel, channel_identity=channel_identity,
            event=event_key, text=event_label, page_id=page_id,
        )
        return _persist_fallback()
    finally:
        # Always release the credit reservation — whether the turn succeeded
        # or crashed. On success, log_accumulated_usage below writes the real
        # deduction to the DB ledger; on failure, no deduction happens and
        # we just free the reservation.
        if reserved_credits > 0:
            release_ai_credits(shop_id=shop_id, reserved_credits=reserved_credits)

    # 5. Deduct credits + write audit log via gateway (single consolidated entry)
    try:
        gateway.log_accumulated_usage(
            usage_type=AIModelUsage.CHAT_COMPLETION,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
        )
    except Exception as exc:
        logger.error("Credit/audit logging failed shop=%s: %s", shop_id, exc)

    # 6. Persist outbound reply + update context cache
    out_ts = int(time.time() * 1000)
    _persist_message(
        shop_id=shop_id, channel=channel, channel_identity=channel_identity,
        direction=MessageDirection.OUTBOUND,
        text=final_reply, timestamp=out_ts,
        page_id=page_id,
    )
    ctx_cache_append(page_id=page_id or channel, psid=channel_identity, role="user", content=combined_inbound_text)
    ctx_cache_append(page_id=page_id or channel, psid=channel_identity, role="assistant", content=final_reply)

    return final_reply


def _handle_credit_exhaustion(*, shop_id: str, page_id: str, psid: str) -> None:
    """Trigger human takeover + notify merchant on credit exhaustion."""
    from chat.services.bot_state import bot_state_set_human_active
    bot_state_set_human_active(page_id=page_id, psid=psid, ttl_minutes=30)
    logger.warning("AI credits exhausted for shop=%s — human takeover triggered", shop_id)
    # TODO: send merchant dashboard WS notification (v0.6 EPIC F)


def _push_dlq_alert(*, shop_id: str, reason: str) -> None:
    """Push a DLQ alert for manual triage."""
    from notifications.services import notification_dlq_push
    try:
        notification_dlq_push(shop_id=shop_id, event_type="AI_ENGINE_FAILURE", metadata={"reason": reason})
    except Exception:
        pass  # DLQ push must never crash the main request path
