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
from marketing.selectors import get_connection_by_page_id
from chat.services.bot_state import bot_state_set_human_active, bot_state_clear_human_active
from chat.channels.send_api import send_text

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
        conn = get_connection_by_page_id(page_id)
        if conn and str(conn.shop_id) == shop_id:
            return conn.access_token
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# EPIC B-01 — Webhook Verification + Ingestion
# ---------------------------------------------------------------------------

class ChannelWebhookView(APIView):
    authentication_classes = []
    permission_classes = []
    channel = None  # Injected by urls.py for legacy route, or fetched from URL kwargs

    def _get_adapter(self, kwargs):
        from chat.channels.registry import get_adapter
        channel_id = self.channel or kwargs.get("channel_id")
        if not channel_id:
            raise ValueError("No channel specified for webhook")
        # Ensure it matches ChannelChoices enum formatting (e.g. FACEBOOK)
        return get_adapter(channel_id.upper())

    def get(self, request, *args, **kwargs):
        """Delegate webhook verification handshake to the channel adapter."""
        try:
            adapter = self._get_adapter(kwargs)
            return adapter.handle_webhook_handshake(request)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    def post(self, request, *args, **kwargs):
        """
        Delegate payload verification and parsing to the channel adapter.
        """
        try:
            adapter = self._get_adapter(kwargs)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        if not adapter.verify_webhook_signature(request):
            logger.warning("%s webhook: invalid signature.", adapter.__class__.__name__)
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        try:
            events = adapter.parse_webhook_payload(request)
            for event in events:
                process_inbound_message.delay(**event)
        except Exception as e:
            logger.error("Error processing %s webhook: %s", adapter.__class__.__name__, e)

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

        from chat.models import ChannelChoices
        from chat.channels.registry import get_adapter
        
        # Legacy frontend payloads only provide psid/page_id for Facebook.
        # Future-proofing: read 'channel' from payload, but default to FACEBOOK.
        channel = d.get("channel", ChannelChoices.FACEBOOK)
        adapter = get_adapter(channel)

        try:
            adapter.send_text(shop_id=shop_id, channel_identity=psid, text=text, page_id=page_id)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        # Persist the agent-sent message
        from chat.models import Conversation
        conversation, created = Conversation.objects.get_or_create(
            shop_id=shop_id,
            tenant_id=shop_id,
            channel=channel,
            channel_identity=psid,
        )
        
        if created or conversation.metadata.get("page_id") != page_id:
            conversation.metadata["page_id"] = page_id
            conversation.save(update_fields=["metadata"])

        ChatMessage.objects.create(
            shop_id=shop_id,
            tenant_id=shop_id,
            conversation=conversation,
            direction=MessageDirection.OUTBOUND,
            text=text,
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
