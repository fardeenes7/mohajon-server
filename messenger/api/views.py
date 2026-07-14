"""
Messenger API views.

Endpoints:
  GET  /api/v1/chat/webhook/         — Meta webhook verification (hub.challenge)
  POST /api/v1/chat/webhook/         — Meta webhook event ingestion
  GET  /api/v1/chat/inbox/           — Conversation list (Omnichannel Inbox)
  GET  /api/v1/chat/inbox/{psid}/    — Full message history for a PSID
  POST /api/v1/chat/takeover/        — Human takeover / handback
  POST /api/v1/chat/send/            — Agent outbound message
  GET  /api/v1/chat/faq/             — List FAQ entries for the shop
  POST /api/v1/chat/faq/             — Create FAQ entry
  PUT  /api/v1/chat/faq/{id}/        — Update FAQ entry
  DELETE /api/v1/chat/faq/{id}/      — Deactivate FAQ entry
"""
from __future__ import annotations

import logging
import time

from django.conf import settings
from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from chat.api.serializers import (
    AgentMessageSerializer,
    ConversationListSerializer,
    FAQEntrySerializer,
    HumanTakeoverSerializer,
)
from chat.models import FAQEntry, ChatMessage, MessageDirection
from chat.selectors import conversation_list_for_shop, message_list_for_psid

from webhooks.services import webhook_signature_valid
from chat.tasks import process_inbound_message, embed_faq_entry
from marketing.models import SocialConnection
from chat.services.bot_state import bot_state_set_human_active, bot_state_clear_human_active
from chat.services.send_api import send_text

logger = logging.getLogger(__name__)


def _get_shop_id(request) -> str:
    """Extract tenant shop ID from request (set by TenantMiddleware)."""
    shop_id = getattr(request, "tenant_id", None)
    if not shop_id:
        raise ValueError("Missing X-Tenant-ID header.")
    return shop_id


def _get_page_token(shop_id: str, page_id: str) -> str | None:
    """Fetch the Page Access Token for the given page from SocialConnection."""
    try:
        conn = SocialConnection.objects.get(
            shop_id=shop_id,
            page_id=page_id,
            deleted_at__isnull=True,
        )
        return conn.access_token
    except Exception:
        return None


# ---------------------------------------------------------------------------
# EPIC B-01 — Webhook Verification + Ingestion
# ---------------------------------------------------------------------------

class MetaWebhookView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        """Meta webhook verification (hub.challenge handshake)."""
        mode = request.query_params.get("hub.mode")
        token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge")

        if mode == "subscribe" and token == settings.META_WEBHOOK_VERIFY_TOKEN:
            return HttpResponse(challenge, content_type="text/plain")
        return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

    def post(self, request):
        """
        Meta webhook event ingestion.
        - Validates X-Hub-Signature-256 HMAC.
        - Returns HTTP 200 immediately (Meta requires < 5s response).
        - Fans out each messaging event as a separate Celery task.
        """
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

            # Resolve shop from page_id
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

                # Determine messaging type
                if "message" in event:
                    msg = event["message"]
                    mid = msg.get("mid", f"unk_{ts}")
                    text = msg.get("text")
                    process_inbound_message.delay(
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
                    process_inbound_message.delay(
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

            # Handle comment events
            for change in entry.get("changes", []):
                if change.get("field") == "feed":
                    val = change.get("value", {})
                    if val.get("item") == "comment" and val.get("verb") == "add":
                        comment_id = val.get("comment_id", "")
                        post_id = val.get("post_id", "")
                        from_data = val.get("from", {})
                        commenter_psid = from_data.get("id", "")
                        process_inbound_message.delay(
                            shop_id=shop_id,
                            page_id=page_id,
                            psid=commenter_psid,
                            message_text=val.get("message"),
                            mid=f"comment_{comment_id}",
                            timestamp=int(val.get("created_time", 0)),
                            messaging_type="comment",
                            comment_data={
                                "comment_id": comment_id,
                                "post_id": post_id,
                                "product_ids": [],  # resolved by marketing app in future
                            },
                            page_access_token=page_access_token,
                        )

        return Response({"status": "ok"})


# ---------------------------------------------------------------------------
# EPIC F-01 — Omnichannel Inbox
# ---------------------------------------------------------------------------

class InboxListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        shop_id = _get_shop_id(request)
        conversations = conversation_list_for_shop(shop_id=shop_id)
        serializer = ConversationListSerializer(conversations, many=True)
        return Response(serializer.data)


class InboxDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, psid: str):
        shop_id = _get_shop_id(request)
        messages = message_list_for_psid(shop_id=shop_id, psid=psid, limit=50)
        return Response(messages)


# ---------------------------------------------------------------------------
# EPIC F-02 — Human Takeover / Handback
# ---------------------------------------------------------------------------

class HumanTakeoverView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = HumanTakeoverSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        page_id = d["page_id"]
        psid = d["psid"]
        action = d["action"]

        if action == "takeover":
            bot_state_set_human_active(page_id=page_id, psid=psid, ttl_minutes=30)
            return Response({"status": "human_active"})
        else:
            bot_state_clear_human_active(page_id=page_id, psid=psid)
            return Response({"status": "bot_resumed"})


# ---------------------------------------------------------------------------
# EPIC F-03 — Agent Outbound Messaging
# ---------------------------------------------------------------------------

class AgentSendView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        shop_id = _get_shop_id(request)
        serializer = AgentMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        page_id = d["page_id"]
        psid = d["psid"]
        text = d["text"]

        token = _get_page_token(shop_id=shop_id, page_id=page_id)
        if not token:
            return Response({"detail": "Page access token not found."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = send_text(psid=psid, text=text, token=token)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        # Persist the agent-sent message
        ChatMessage.objects.create(
            shop_id=shop_id,
            tenant_id=shop_id,
            psid=psid,
            page_id=page_id,
            direction=MessageDirection.OUTBOUND,
            message_text=text,
            mid=result.get("message_id", f"agent_{int(time.time() * 1000)}"),
            timestamp=int(time.time() * 1000),
        )
        return Response({"status": "sent"})


# ---------------------------------------------------------------------------
# EPIC G-01 — FAQ Management
# ---------------------------------------------------------------------------

class FAQListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        shop_id = _get_shop_id(request)
        entries = FAQEntry.objects.filter(shop_id=shop_id, deleted_at__isnull=True).order_by("sort_order", "created_at")
        return Response(FAQEntrySerializer(entries, many=True).data)

    def post(self, request):
        shop_id = _get_shop_id(request)
        serializer = FAQEntrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = serializer.save(shop_id=shop_id, tenant_id=shop_id)
        # Trigger async embedding generation
        embed_faq_entry.delay(faq_entry_id=str(entry.id))
        return Response(FAQEntrySerializer(entry).data, status=status.HTTP_201_CREATED)


class FAQDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_entry(self, shop_id: str, pk: str):
        try:
            return FAQEntry.objects.get(id=pk, shop_id=shop_id, deleted_at__isnull=True)
        except FAQEntry.DoesNotExist:
            return None

    def put(self, request, pk: str):
        shop_id = _get_shop_id(request)
        entry = self._get_entry(shop_id, pk)
        if not entry:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = FAQEntrySerializer(entry, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        entry = serializer.save()
        # Re-embed on content change
        embed_faq_entry.delay(faq_entry_id=str(entry.id))
        return Response(FAQEntrySerializer(entry).data)

    def delete(self, request, pk: str):
        shop_id = _get_shop_id(request)
        entry = self._get_entry(shop_id, pk)
        if not entry:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        entry.is_active = False
        entry.save(update_fields=["is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)
