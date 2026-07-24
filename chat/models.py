from __future__ import annotations

import uuid
from django.db import models
from pgvector.django import VectorField

from core.models import SoftDeleteModel, TenantModel, VectorStatus

class ChannelChoices(models.TextChoices):
    FACEBOOK = "FACEBOOK", "Facebook Messenger"
    WEB_WIDGET = "WEB_WIDGET", "Web Widget"
    WHATSAPP = "WHATSAPP", "WhatsApp"

class MessageDirection(models.TextChoices):
    INBOUND = "INBOUND", "Inbound"
    OUTBOUND = "OUTBOUND", "Outbound"
    # Non-conversational timeline events (takeover, handback, bot paused, …).
    # Rendered as inline markers in the agent inbox, never sent to the customer.
    SYSTEM = "SYSTEM", "System"

class FAQCategory(models.TextChoices):
    FAQ = "FAQ", "FAQ"
    RETURN_POLICY = "RETURN_POLICY", "Return Policy"
    SHIPPING_POLICY = "SHIPPING_POLICY", "Shipping Policy"
    TERMS_OF_SERVICE = "TERMS_OF_SERVICE", "Terms of Service"
    CUSTOM = "CUSTOM", "Custom"

class Conversation(TenantModel):
    """
    Channel-agnostic grouping of messages.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    channel = models.CharField(max_length=20, choices=ChannelChoices.choices)
    channel_identity = models.CharField(max_length=255, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    # Layer 2 identity seam: the durable ChannelActor behind this conversation.
    # Conversation keeps grouping messages; identity moves to the actor.
    # SET_NULL so identity graph churn never cascades into message history.
    actor = models.ForeignKey(
        "identity.ChannelActor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
    )
    # Read-state for the agent inbox. Bumped on inbound persist, reset when an
    # agent opens the conversation. has_unread is denormalised from unread_count
    # so the inbox list can filter/index without a computed expression.
    has_unread = models.BooleanField(default=False, db_index=True)
    unread_count = models.PositiveIntegerField(default=0)
    last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["shop", "channel", "channel_identity"], name="conv_shop_chan_id_idx"),
        ]
        constraints = [
            # A channel actor (PSID/waid/web-session) has exactly one live
            # conversation per shop+channel. Without this, concurrent inbound
            # messages race through get_or_create into duplicate conversations.
            # Partial (deleted_at IS NULL) so a soft-deleted row can be replaced,
            # mirroring the StockLocation default-location constraint.
            models.UniqueConstraint(
                fields=["shop", "channel", "channel_identity"],
                condition=models.Q(deleted_at__isnull=True),
                name="uq_conversation_shop_channel_identity",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.channel}] {self.channel_identity}"


class ChatMessage(TenantModel):
    """
    Durable store for every inbound and outbound message.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="chat_messages",
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    direction = models.CharField(
        max_length=8,
        choices=MessageDirection.choices,
        default=MessageDirection.INBOUND,
    )
    text = models.TextField(blank=True, null=True)
    attachment_payload = models.JSONField(blank=True, null=True)
    timestamp = models.BigIntegerField(db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["conversation", "timestamp"], name="chatmsg_conv_ts_idx"),
            models.Index(fields=["created_at"], name="chatmsg_created_at_idx"),
        ]

    def __str__(self) -> str:
        return f"[{self.direction}] {self.text[:60] if self.text else '(attachment)'}"


class FAQEntry(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="faq_entries",
    )
    category = models.CharField(
        max_length=20,
        choices=FAQCategory.choices,
        default=FAQCategory.FAQ,
        db_index=True,
    )
    question = models.TextField()
    answer = models.TextField()
    embedding = VectorField(dimensions=3072, null=True, blank=True)
    vector_status = models.CharField(
        max_length=20,
        choices=VectorStatus.choices,
        default=VectorStatus.PENDING,
        db_index=True,
    )
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveIntegerField(default=0, db_index=True)

    class Meta:
        ordering = ["sort_order", "created_at"]
        indexes = [
            models.Index(
                fields=["shop", "is_active", "sort_order"],
                name="chat_faq_shop_active_order_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.category}] {self.question[:80]}"


class WhatsAppConfig(TenantModel):
    """
    Configuration and credentials for the WhatsApp Business API.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.OneToOneField(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="whatsapp_config",
    )
    phone_number_id = models.CharField(max_length=255, db_index=True)
    waba_id = models.CharField(max_length=255)
    access_token = models.TextField()
    is_active = models.BooleanField(default=True, db_index=True)

    def __str__(self) -> str:
        return f"WhatsApp Config: {self.shop.name}"

