import logging
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden

from chat.channels.base import BaseChannelAdapter
from webhooks.services import webhook_signature_valid
from marketing.selectors import get_connection_by_page_id
from chat.channels.send_api import send_text

logger = logging.getLogger(__name__)

class FacebookAdapter(BaseChannelAdapter):
    
    def handle_webhook_handshake(self, request: HttpRequest) -> HttpResponse:
        mode = request.query_params.get("hub.mode")
        token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge")

        if mode == "subscribe" and token == settings.META_WEBHOOK_VERIFY_TOKEN:
            return HttpResponse(challenge, content_type="text/plain")
        
        return HttpResponseForbidden("Forbidden")

    def verify_webhook_signature(self, request: HttpRequest) -> bool:
        signature = request.headers.get("X-Hub-Signature-256", "")
        body = request.body
        return webhook_signature_valid(signature=signature, body=body)

    def parse_webhook_payload(self, request: HttpRequest) -> list[dict[str, Any]]:
        data = request.data
        events = []
        
        if data.get("object") != "page":
            return events

        for entry in data.get("entry", []):
            page_id = str(entry.get("id", ""))

            try:
                conn = get_connection_by_page_id(page_id)
                if not conn:
                    raise Exception("No connection")
                shop_id = str(conn.shop_id)
                page_access_token = conn.access_token
            except Exception:
                logger.warning("No SocialConnection found for page_id=%s", page_id)
                continue

            for event in entry.get("messaging", []):
                psid = str(event.get("sender", {}).get("id", ""))
                ts = int(event.get("timestamp", 0))

                if "message" in event:
                    msg = event["message"]
                    mid = msg.get("mid", f"unk_{ts}")
                    text = msg.get("text")
                    events.append({
                        "shop_id": shop_id,
                        "page_id": page_id,
                        "psid": psid,
                        "message_text": text,
                        "mid": mid,
                        "timestamp": ts,
                        "messaging_type": "message",
                        "page_access_token": page_access_token,
                    })
                elif "postback" in event:
                    payload = event["postback"].get("payload", "")
                    events.append({
                        "shop_id": shop_id,
                        "page_id": page_id,
                        "psid": psid,
                        "message_text": None,
                        "mid": f"postback_{ts}",
                        "timestamp": ts,
                        "messaging_type": "postback",
                        "postback_payload": payload,
                        "page_access_token": page_access_token,
                    })

            for change in entry.get("changes", []):
                if change.get("field") == "feed":
                    val = change.get("value", {})
                    if val.get("item") == "comment" and val.get("verb") == "add":
                        comment_id = val.get("comment_id", "")
                        post_id = val.get("post_id", "")
                        from_data = val.get("from", {})
                        commenter_psid = from_data.get("id", "")
                        events.append({
                            "shop_id": shop_id,
                            "page_id": page_id,
                            "psid": commenter_psid,
                            "message_text": val.get("message"),
                            "mid": f"comment_{comment_id}",
                            "timestamp": int(val.get("created_time", 0)),
                            "messaging_type": "comment",
                            "comment_data": {
                                "comment_id": comment_id,
                                "post_id": post_id,
                                "product_ids": [],
                            },
                            "page_access_token": page_access_token,
                        })
        return events

    def send_text(self, shop_id: str, channel_identity: str, text: str, **kwargs: Any) -> None:
        page_id = kwargs.get("page_id")
        
        if not page_id:
            # Fallback if AgentSendView didn't provide it
            from chat.models import Conversation
            try:
                conversation = Conversation.objects.get(
                    shop_id=shop_id,
                    channel="FACEBOOK",
                    channel_identity=channel_identity
                )
                page_id = conversation.metadata.get("page_id")
            except Exception:
                pass
                
        if not page_id:
            raise ValueError(f"Could not resolve page_id for FACEBOOK {channel_identity}")

        try:
            conn = get_connection_by_page_id(page_id)
            if not conn or str(conn.shop_id) != shop_id:
                raise Exception("Not found or shop mismatch")
            token = conn.access_token
        except Exception as e:
            raise ValueError(f"Could not resolve credentials for FACEBOOK {channel_identity}: {e}")

        send_text(psid=channel_identity, text=text, token=token)
