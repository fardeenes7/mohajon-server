"""
Messenger API serializers.
"""
from __future__ import annotations

from rest_framework import serializers

from chat.models import ChatMessage, FAQEntry


class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = ["id", "psid", "page_id", "direction", "message_text", "attachment_payload", "mid", "timestamp", "created_at"]
        read_only_fields = fields


class FAQEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = FAQEntry
        fields = ["id", "category", "question", "answer", "is_active", "sort_order", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class ConversationListSerializer(serializers.Serializer):
    psid = serializers.CharField()
    page_id = serializers.CharField()
    last_ts = serializers.IntegerField()


class HumanTakeoverSerializer(serializers.Serializer):
    page_id = serializers.CharField()
    psid = serializers.CharField()
    action = serializers.ChoiceField(choices=["takeover", "handback"])


class AgentMessageSerializer(serializers.Serializer):
    page_id = serializers.CharField()
    psid = serializers.CharField()
    text = serializers.CharField(max_length=2000)
