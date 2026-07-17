import logging
from typing import Any
import requests

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden

from chat.channels.base import BaseChannelAdapter
from webhooks.services import webhook_signature_valid

logger = logging.getLogger(__name__)

class WhatsAppAdapter(BaseChannelAdapter):

    def handle_webhook_handshake(self, request: HttpRequest) -> HttpResponse:
        mode = request.query_params.get("hub.mode")
        token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge")

        # Meta uses the exact same verification scheme for WhatsApp as it does for Messenger
        if mode == "subscribe" and token == settings.META_WEBHOOK_VERIFY_TOKEN:
            return HttpResponse(challenge, content_type="text/plain")
        
        return HttpResponseForbidden("Forbidden")

    def verify_webhook_signature(self, request: HttpRequest) -> bool:
        signature = request.headers.get("X-Hub-Signature-256", "")
        body = request.body
        # Uses the same APP_SECRET as Messenger
        return webhook_signature_valid(signature=signature, body=body)

    def parse_webhook_payload(self, request: HttpRequest) -> list[dict[str, Any]]:
        from chat.models import WhatsAppConfig
        
        data = request.data
        events = []
        
        if data.get("object") != "whatsapp_business_account":
            return events

        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if value.get("messaging_product") != "whatsapp":
                    continue
                    
                phone_number_id = value.get("metadata", {}).get("phone_number_id")
                if not phone_number_id:
                    continue

                # Resolve shop from phone_number_id
                try:
                    config = WhatsAppConfig.objects.get(phone_number_id=phone_number_id, is_active=True)
                    shop_id = str(config.shop_id)
                except WhatsAppConfig.DoesNotExist:
                    logger.warning("No WhatsAppConfig found for phone_number_id=%s", phone_number_id)
                    continue

                # Parse messages
                for msg in value.get("messages", []):
                    sender_psid = msg.get("from")
                    mid = msg.get("id")
                    # WhatsApp provides timestamps in seconds, convert to standard ms used by chat app if needed?
                    # Wait, Facebook Messenger timestamp is in ms. WhatsApp timestamp is in seconds.
                    # Let's align with the ms timestamp that the system expects.
                    ts_seconds = int(msg.get("timestamp", 0))
                    ts_ms = ts_seconds * 1000
                    
                    msg_type = msg.get("type")
                    
                    text = None
                    if msg_type == "text":
                        text = msg.get("text", {}).get("body")
                    
                    # Note: WhatsApp uses phone numbers instead of PSIDs. 
                    # We map `sender_psid` to `psid` so the generic system works.
                    # We map `phone_number_id` to `page_id`.
                    events.append({
                        # WhatsApp waid IS the phone number — it must be stored
                        # against the WHATSAPP value space, never as a FACEBOOK
                        # psid. This channel tag flows through
                        # process_inbound_message → run_ai_turn → _persist_message
                        # → get_or_create_channel_actor, where the waid mints a
                        # PHONE ContactPoint.
                        "channel": "WHATSAPP",
                        "shop_id": shop_id,
                        "page_id": phone_number_id,
                        "psid": sender_psid,
                        "message_text": text,
                        "mid": mid,
                        "timestamp": ts_ms,
                        "messaging_type": "message",
                        "page_access_token": config.access_token,
                    })

        return events

    def send_text(self, shop_id: str, channel_identity: str, text: str, **kwargs: Any) -> None:
        from chat.models import WhatsAppConfig
        
        try:
            config = WhatsAppConfig.objects.get(shop_id=shop_id, is_active=True)
        except WhatsAppConfig.DoesNotExist:
            raise ValueError(f"No active WhatsAppConfig for shop {shop_id}")

        url = f"https://graph.facebook.com/v21.0/{config.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {config.access_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": channel_identity,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": text
            }
        }
        
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
