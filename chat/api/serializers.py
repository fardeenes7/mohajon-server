"""
Messenger API serializers.
"""
from __future__ import annotations

from rest_framework import serializers

from chat.models import Conversation, ChatMessage, FAQEntry


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
        fields = ["id", "channel", "channel_identity", "metadata", "updated_at"]


class HumanTakeoverSerializer(serializers.Serializer):
    page_id = serializers.CharField()
    psid = serializers.CharField()
    action = serializers.ChoiceField(choices=["takeover", "handback"])


class AgentMessageSerializer(serializers.Serializer):
    page_id = serializers.CharField()
    psid = serializers.CharField()
    text = serializers.CharField(max_length=2000)
