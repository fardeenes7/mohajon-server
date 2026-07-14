from __future__ import annotations

import logging
import time

from django.conf import settings
from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from marketing.models import SocialConnection
from webhooks.services import webhook_signature_valid
from chat.tasks import process_inbound_message
from chat.channels.api import send_text # Need to implement or move

logger = logging.getLogger(__name__)

def _get_shop_id(request) -> str:
    shop_id = getattr(request, "tenant_id", None)
    if not shop_id:
        raise ValueError("Missing X-Tenant-ID header.")
    return shop_id

def _get_page_token(shop_id: str, page_id: str) -> str | None:
    try:
        conn = SocialConnection.objects.get(
            shop_id=shop_id,
            page_id=page_id,
            deleted_at__isnull=True,
        )
        return conn.access_token
    except Exception:
        return None

class FacebookWebhookView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        mode = request.query_params.get("hub.mode")
        token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge")

        if mode == "subscribe" and token == settings.META_WEBHOOK_VERIFY_TOKEN:
            return HttpResponse(challenge, content_type="text/plain")
        return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

    def post(self, request):
        signature = request.headers.get("X-Hub-Signature-256", "")
        body = request.body

        if not webhook_signature_valid(signature=signature, body=body):
            logger.warning("Meta webhook: invalid HMAC signature.")
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        data = request.data
        if data.get("object") != "page":
            return Response({"status": "ignored"})

        for entry in data.get("entry", []):
            page_id = str(entry.get("id", ""))
            try:
                conn = SocialConnection.objects.get(page_id=page_id, deleted_at__isnull=True)
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
                    process_facebook_message.delay(
                        shop_id=shop_id,
                        page_id=page_id,
                        psid=psid,
                        message_text=text,
                        mid=mid,
                        timestamp=ts,
                        messaging_type="message",
                        page_access_token=page_access_token,
                    )
                elif "postback" in event:
                    payload = event["postback"].get("payload", "")
                    process_facebook_message.delay(
                        shop_id=shop_id,
                        page_id=page_id,
                        psid=psid,
                        message_text=None,
                        mid=f"postback_{ts}",
                        timestamp=ts,
                        messaging_type="postback",
                        postback_payload=payload,
                        page_access_token=page_access_token,
                    )

            # Comments handling omitted for brevity but follows same pattern
        return Response({"status": "ok"})
