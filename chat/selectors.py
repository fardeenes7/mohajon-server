from __future__ import annotations

from typing import List, Dict, Any
from chat.models import Conversation, ChatMessage

def conversation_list_for_shop(*, shop_id: str):
    return Conversation.objects.filter(
        shop_id=shop_id
    ).order_by("-updated_at")

def message_list_for_psid(*, shop_id: str, psid: str, limit: int = 20) -> List[Dict[str, Any]]:
    # psid maps to channel_identity in the new model
    messages = ChatMessage.objects.filter(
        shop_id=shop_id,
        conversation__channel_identity=psid
    ).order_by("-timestamp")[:limit]
    
    # Needs to return a list of dicts with role and content for the engine
    results = []
    for msg in reversed(messages):
        role = "assistant" if msg.direction == "OUTBOUND" else "user"
        results.append({
            "role": role,
            "content": msg.text or ""
        })
    return results
