"""
Messenger app models.

Covers:
  - MessengerMessage  — durable conversation history (Meta provides no retrieval API)
  - FAQEntry          — shop-level policies & FAQ with pgvector semantic embeddings
"""
from __future__ import annotations

import uuid

from django.db import models
from pgvector.django import VectorField

from core.models import SoftDeleteModel, TenantModel, VectorStatus


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class MessageDirection(models.TextChoices):
    INBOUND = "INBOUND", "Inbound"
    OUTBOUND = "OUTBOUND", "Outbound"


class FAQCategory(models.TextChoices):
    FAQ = "FAQ", "FAQ"
    RETURN_POLICY = "RETURN_POLICY", "Return Policy"
    SHIPPING_POLICY = "SHIPPING_POLICY", "Shipping Policy"
    TERMS_OF_SERVICE = "TERMS_OF_SERVICE", "Terms of Service"
    CUSTOM = "CUSTOM", "Custom"


# ---------------------------------------------------------------------------
# MessengerMessage  (EPIC A-01)
# ---------------------------------------------------------------------------

class MessengerMessage(TenantModel):
    """
    Durable store for every inbound and outbound Messenger message.

    The Meta Graph API does NOT provide a conversation history retrieval
    endpoint — this table is the sole source of truth for all AI context
    windows and audit needs.

    Retention policy: records are soft-deleted after 30 days per
    global_business_rules_and_limits.md §4.
    GDPR: hard_delete_account() cascade must anonymise / delete these records.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="messenger_messages",
    )
    # Page-Scoped User ID — opaque identifier assigned by Meta per page.
    psid = models.CharField(max_length=255, db_index=True)
    # The Facebook Page ID that received / sent the message.
    page_id = models.CharField(max_length=255, db_index=True)
    direction = models.CharField(
        max_length=8,
        choices=MessageDirection.choices,
        default=MessageDirection.INBOUND,
    )
    message_text = models.TextField(blank=True, null=True)
    # Stores full template payloads for outbound structured messages.
    attachment_payload = models.JSONField(blank=True, null=True)
    # Meta message ID — used for deduplication.  Must be unique per direction.
    mid = models.CharField(max_length=255, unique=True)
    # Original Meta event timestamp (Unix milliseconds).
    timestamp = models.BigIntegerField(db_index=True)

    class Meta:
        indexes = [
            # Fast context-window queries: last N messages per PSID per shop.
            models.Index(
                fields=["shop", "psid", "timestamp"],
                name="msg_shop_psid_ts_idx",
            ),
            # Retention sweep: find records older than 30 days.
            models.Index(
                fields=["created_at"],
                name="msg_created_at_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.direction}] {self.psid} — {self.message_text[:60] if self.message_text else '(attachment)'}"


# ---------------------------------------------------------------------------
# FAQEntry  (EPIC A-02)
# ---------------------------------------------------------------------------

class FAQEntry(TenantModel):
    """
    Merchant-managed FAQ & shop policy entries.

    Embeddings are populated asynchronously by a Celery task using
    OpenAI text-embedding-3-small after every create / update.  The AI
    calls search_faq() which performs a pgvector cosine similarity search
    and only returns results above a 0.75 threshold.

    Platform Privacy Policy is NOT stored here — it is a platform-level
    document controlled by Mohajon (global_business_rules_and_limits.md §5).
    """

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
    # 1536-dim vector from text-embedding-3-small (populated by Celery).
    embedding = VectorField(dimensions=1536, null=True, blank=True)
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
                name="faq_shop_active_order_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.category}] {self.question[:80]}"
