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
from typing import Any

from django.db import transaction
from django.utils import timezone

from core.models import AIModelUsage
from core.services.ai_credits import has_sufficient_ai_credits
from core.services.ai_gateway import AIGateway
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
    from shops.models import Shop, ShopSettings

    try:
        shop = Shop.objects.get(id=shop_id, deleted_at__isnull=True)
        shop_name = shop.name
        currency = shop.base_currency
    except Shop.DoesNotExist:
        shop_name = "Our Shop"
        currency = "BDT"

    now_str = timezone.now().strftime("%A, %d %B %Y, %I:%M %p UTC")

    return (
        f"You are a helpful, friendly AI shopping assistant for **{shop_name}**.\n"
        f"Current date and time: {now_str}.\n"
        f"All prices are in {currency}.\n\n"
        "## Core Instructions\n"
        "- Always respond in the same language as the customer's message.\n"
        "- Call a function when uncertain rather than hallucinating an answer.\n"
        "- NEVER call `confirm_order` directly. Always call `prepare_order_draft` first, "
        "present the summary, and wait for explicit customer confirmation via a Messenger button.\n"
        "- If a question is about shop policies, call `search_faq` before attempting to answer "
        "from your own knowledge.\n"
        "- If `confidence < 0.65`, ask a clarifying question before calling any mutating function.\n"
        "- Never expose raw error strings or stack traces to the customer.\n"
        "- If `search_faq` returns no results above the threshold, inform the customer you cannot "
        "answer and offer to escalate to a human agent.\n"
    )


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
) -> None:
    from chat.models import Conversation, ChatMessage
    
    conversation, _ = Conversation.objects.get_or_create(
        shop_id=shop_id,
        tenant_id=shop_id,
        channel=channel,
        channel_identity=channel_identity,
    )
    
    ChatMessage.objects.create(
        shop_id=shop_id,
        tenant_id=shop_id,
        conversation=conversation,
        direction=direction,
        text=text,
        attachment_payload=attachment_payload,
        timestamp=timestamp,
    )


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
    context_window_size: int = 20,
    fallback_message: str | None = None,
) -> str:
    """
    Execute one AI turn:
      1. Persist the inbound message.
      2. Load context (Redis → Postgres fallback).
      3. Check credit balance.
      4. Run OpenAI tool-call loop (max 5 calls).
      5. Deduct credits.
      6. Persist outbound reply.
      7. Return the final text to send to the customer.

    On any failure, returns the fallback_message string.
    """
    import time

    # 1. Persist inbound message
    _persist_message(
        shop_id=shop_id, channel=channel, channel_identity=channel_identity,
        direction=MessageDirection.INBOUND,
        text=inbound_text, timestamp=inbound_timestamp,
    )

    # 2. Load context
    ctx_messages = ctx_cache_get(page_id=channel, psid=channel_identity)
    if ctx_messages is None:
        # Cold path — load from Postgres and warm the cache
        db_messages = message_list_for_psid(
            shop_id=shop_id, psid=channel_identity, limit=context_window_size
        )
        ctx_cache_populate(page_id=channel, psid=channel_identity, messages=db_messages, max_size=context_window_size)
        ctx_messages = db_messages

    # Build messages list for OpenAI
    openai_messages: list[dict[str, Any]] = [
        {"role": "system", "content": _build_system_prompt(shop_id=shop_id)}
    ]
    for m in ctx_messages[-context_window_size:]:
        openai_messages.append({"role": m["role"], "content": m["content"]})
    openai_messages.append({"role": "user", "content": inbound_text})

    # 3. Credit pre-check
    if not has_sufficient_ai_credits(shop_id=shop_id):
        _handle_credit_exhaustion(shop_id=shop_id, page_id=channel, psid=channel_identity)
        return fallback_message or "I'm having a little trouble right now. Our team will reach out shortly! 🙏"

    # 4. OpenAI tool-call loop — all AI concerns routed through AIGateway
    gateway = AIGateway(shop_id=shop_id, reference_id=channel_identity)
    total_input_tokens = 0
    total_output_tokens = 0
    tool_call_depth = 0
    final_reply = fallback_message or "I'm having a little trouble right now. Our team will reach out shortly! 🙏"

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
                        page_id=channel,
                    )
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result),
                    })
            else:
                final_reply = (msg.content or "").strip()
                break

    except Exception as exc:
        logger.error("AI engine error shop=%s identity=%s: %s", shop_id, channel_identity, exc)
        _push_dlq_alert(shop_id=shop_id, reason=str(exc))
        return fallback_message or "I'm having a little trouble right now. Our team will reach out shortly! 🙏"

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
    )
    ctx_cache_append(page_id=channel, psid=channel_identity, role="user", content=inbound_text)
    ctx_cache_append(page_id=channel, psid=channel_identity, role="assistant", content=final_reply)

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
