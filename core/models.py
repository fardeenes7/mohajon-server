from django.db import models
from django.db.models import Q
from django.utils import timezone
from .managers import SoftDeleteManager

class SoftDeleteModel(models.Model):
    """
    Abstract base class that provides soft-deletion capabilities.
    """
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        self.deleted_at = timezone.now()
        self.save(update_fields=['deleted_at'])
        
    def hard_delete(self):
        super().delete()

    def restore(self):
        self.deleted_at = None
        self.save(update_fields=['deleted_at'])

class TenantModel(SoftDeleteModel):
    """
    Abstract base class ensuring row-level multitenancy isolation.
    """
    tenant_id = models.UUIDField(db_index=True, help_text="Tenant that owns this record")

    class Meta:
        abstract = True


class VectorStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    CREATED = "CREATED", "Created"
    SKIPPED = "SKIPPED", "Skipped"
    FAILED = "FAILED", "Failed"


class AIModelProvider(models.TextChoices):
    OPENAI = "OPENAI", "OpenAI"
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
