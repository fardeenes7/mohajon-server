import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from core.models import SoftDeleteModel, TenantModel

class AIModelProvider(models.TextChoices):
    OPENAI = "OPENAI", "OpenAI"
    GOOGLE = "GOOGLE", "Google"
    ANTHROPIC = "ANTHROPIC", "Anthropic"
    STABILITY = "STABILITY", "Stability AI"
    CUSTOM = "CUSTOM", "Custom"

class AIModelUsage(models.TextChoices):
    CHAT_COMPLETION = "CHAT_COMPLETION", "Chat Completion"
    EMBEDDING = "EMBEDDING", "Embedding"
    IMAGE_GENERATION = "IMAGE_GENERATION", "Image Generation"

class AIModelRegistry(SoftDeleteModel):
    """
    Platform-level dynamic model routing registry.

    A single active default can be configured per usage type; a fallback
    ordering (`priority`) supports safe failover and phased rollouts.
    """
    usage = models.CharField(max_length=32, choices=AIModelUsage.choices, db_index=True)
    provider = models.CharField(max_length=20, choices=AIModelProvider.choices, default=AIModelProvider.OPENAI)
    model_name = models.CharField(max_length=100)
    display_name = models.CharField(max_length=120, blank=True)

    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    priority = models.PositiveIntegerField(default=100)

    input_price_per_1m_tokens = models.DecimalField(max_digits=14, decimal_places=6, null=True, blank=True)
    output_price_per_1m_tokens = models.DecimalField(max_digits=14, decimal_places=6, null=True, blank=True)
    image_price_per_call = models.DecimalField(max_digits=14, decimal_places=6, null=True, blank=True)

    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["usage", "priority", "model_name"]
        indexes = [
            models.Index(fields=["usage", "is_active", "priority"], name="ai_model_usage_active_pri_idx"),
            models.Index(fields=["provider", "usage"], name="ai_model_provider_usage_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["usage", "provider", "model_name"],
                condition=Q(deleted_at__isnull=True),
                name="uq_ai_model_usage_provider_model_active",
            ),
            models.UniqueConstraint(
                fields=["usage"],
                condition=Q(is_default=True, deleted_at__isnull=True),
                name="uq_ai_model_single_default_per_usage",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.usage}::{self.provider}::{self.model_name}"

class AIUsageLog(TenantModel):
    """
    Detailed audit log for every AI invocation.

    Used for:
      1. Merchant billing & usage dashboards (EPIC B)
      2. Quality control & request debugging
      3. Precise credit tracking
    """
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="ai_usage_logs",
    )
    usage_type = models.CharField(max_length=32, choices=AIModelUsage.choices, db_index=True)
    provider = models.CharField(max_length=20, choices=AIModelProvider.choices)
    model_name = models.CharField(max_length=100)

    # Request & Response metadata
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)

    # Financials
    # Stored with high precision to avoid rounding errors on tiny requests
    usd_cost = models.DecimalField(max_digits=14, decimal_places=8, default=0)
    credits_deducted = models.DecimalField(max_digits=14, decimal_places=4, default=0)

    # Context (optional link to what triggered it, e.g. Messenger Message ID)
    reference_id = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    # Stores raw response snippets or tool call summaries
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["shop", "created_at"], name="ai_usage_shop_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.shop_id} | {self.usage_type} | {self.credits_deducted} credits"


class AICreditCategory(models.TextChoices):
    PURCHASED = "PURCHASED", "Purchased"
    BONUS = "BONUS", "Bonus"
    PROMOTION = "PROMOTION", "Promotion"
    REFERRAL = "REFERRAL", "Referral"
    ADJUSTMENT = "ADJUSTMENT", "Manual Adjustment"


class AICreditLot(TenantModel):
    """
    A single grant of AI credits ("lot") with a category and optional expiry.

    The ledger of lots is the SOURCE OF TRUTH for a shop's usable AI credit
    balance. Deductions draw from lots FIFO by soonest expiry (non-expiring
    lots last); the flat ``ShopSettings.ai_credit_balance`` is kept as a
    synced cache for existing readers (dashboards, etc.).

    Sources of credit — purchases, referral/affiliate bonuses, promotions,
    and manual platform-admin adjustments — all become lots via
    ``ai.services.ai_credits.grant_ai_credits``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="ai_credit_lots",
    )
    category = models.CharField(
        max_length=20,
        choices=AICreditCategory.choices,
        db_index=True,
    )

    # Original grant amount and the portion still spendable.
    credits_granted = models.DecimalField(max_digits=12, decimal_places=2)
    credits_remaining = models.DecimalField(max_digits=12, decimal_places=2)

    # null expiry = credits never expire.
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # State flags maintained by the service / sweep task.
    is_exhausted = models.BooleanField(default=False)  # credits_remaining == 0
    is_expired = models.BooleanField(default=False)  # swept past expires_at

    # Link back to the purchase that funded a PURCHASED lot (if any).
    source_topup = models.ForeignKey(
        "billing.AICreditTopUp",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="credit_lots",
    )

    note = models.CharField(max_length=255, blank=True)  # admin-entered reason
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="granted_ai_credit_lots",
    )

    class Meta:
        # Soonest-expiry first so FIFO draw is the natural ordering; nulls
        # (non-expiring) sort last in the active-lots query.
        ordering = ["expires_at", "created_at"]
        indexes = [
            models.Index(
                fields=["shop", "is_expired", "is_exhausted", "expires_at"],
                name="ai_credit_lot_active_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.shop_id} | {self.category} | {self.credits_remaining}/{self.credits_granted}"
