"""
Messenger API serializers.
"""
from __future__ import annotations

from rest_framework import serializers

from chat.models import Conversation, ChatMessage, FAQEntry, ChannelChoices


class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = ["id", "conversation", "direction", "text", "attachment_payload", "timestamp", "created_at"]
        read_only_fields = fields


class FAQEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = FAQEntry
        fields = ["id", "category", "question", "answer", "is_active", "sort_order", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class ConversationListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = [
            "id", "channel", "channel_identity", "metadata", "updated_at",
            "page_id", "display_name", "has_unread", "unread_count", "last_read_at",
            "last_message_text", "last_message_direction", "last_message_at",
            "human_active", "bot_active",
        ]

    page_id = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()
    human_active = serializers.SerializerMethodField()
    bot_active = serializers.SerializerMethodField()

    def get_human_active(self, obj) -> bool:
        # Populated from a batched Redis lookup passed in via serializer context
        # (see InboxListView) to avoid an N+1 Redis round-trip per row.
        m = self.context.get("human_active_map") or {}
        return bool(m.get((obj.metadata.get("page_id") or "", obj.channel_identity), False))

    def get_bot_active(self, obj) -> bool:
        # The bot auto-responds only when globally in BOT mode AND no human has
        # taken over this specific conversation.
        from chat.constants import bot_autoresponds
        return bot_autoresponds() and not self.get_human_active(obj)
    # Populated by the conversation_list_for_shop annotations; default to None so
    # the serializer also works on un-annotated instances (e.g. realtime pushes).
    last_message_text = serializers.CharField(read_only=True, default=None)
    last_message_direction = serializers.CharField(read_only=True, default=None)
    last_message_at = serializers.IntegerField(read_only=True, default=None)

    def get_page_id(self, obj) -> str | None:
        return obj.metadata.get("page_id")

    def get_display_name(self, obj) -> str:
        # Prefer a resolved profile name if the identity layer stored one,
        # otherwise fall back to the raw channel identity (PSID / waid).
        meta = obj.metadata or {}
        return (
            meta.get("name")
            or meta.get("full_name")
            or meta.get("profile_name")
            or obj.channel_identity
        )


class HumanTakeoverSerializer(serializers.Serializer):
    page_id = serializers.CharField()
    psid = serializers.CharField()
    action = serializers.ChoiceField(choices=["takeover", "handback"])
    channel = serializers.ChoiceField(choices=ChannelChoices.choices, required=False, default=ChannelChoices.FACEBOOK)


class AgentMessageSerializer(serializers.Serializer):
    page_id = serializers.CharField(required=False, allow_blank=True, default="")
    psid = serializers.CharField()
    text = serializers.CharField(max_length=2000)
    channel = serializers.ChoiceField(choices=ChannelChoices.choices, required=False, default=ChannelChoices.FACEBOOK)

from chat.models import WhatsAppConfig

class WhatsAppConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = WhatsAppConfig
        fields = ["id", "phone_number_id", "waba_id", "is_active"]
        read_only_fields = ["id"]

class WhatsAppOAuthPageSerializer(serializers.Serializer):
    id = serializers.CharField()
    display_phone_number = serializers.CharField()
    name = serializers.CharField()
    waba_id = serializers.CharField()

