"""
Product (master) model.

One Product = a buyable item. Variants are the actual purchasable SKUs.
Master stock = sum of all variant stocks (computed property, not stored).

Product lifecycle states (per Phase 0.3 spec):
  DRAFT       → hidden, not visible on storefront
  PUBLISHED   → live, fully visible
  SCHEDULED   → will auto-publish at `publish_at` datetime
  OUT_OF_STOCK→ visible, button disabled (stock = 0 on ALL variants)
  ARCHIVED    → hidden but preserved for historical order references
"""
import uuid

from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.utils.text import slugify
from pgvector.django import VectorField

from core.models import TenantModel, VectorStatus


class ProductStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    PUBLISHED = "PUBLISHED", "Published"
    SCHEDULED = "SCHEDULED", "Scheduled"
    OUT_OF_STOCK = "OUT_OF_STOCK", "Out of Stock"
    ARCHIVED = "ARCHIVED", "Archived"


class Product(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="products",
        db_index=False,  # covered by compound indexes below
    )
    category = models.ForeignKey(
        "catalog.Category",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
    )

    # ── Core identity ──────────────────────────────────────────────────────
    name = models.CharField(max_length=500)
    slug = models.CharField(max_length=500, db_index=False)
    description = models.TextField(blank=True)  # Stored as HTML from Tiptap/Quill
    status = models.CharField(
        max_length=15,
        choices=ProductStatus.choices,
        default=ProductStatus.DRAFT,
    )
    publish_at = models.DateTimeField(null=True, blank=True)

    # ── Pricing & tax ──────────────────────────────────────────────────────
    base_price = models.DecimalField(max_digits=10, decimal_places=2)
    compare_at_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    purchase_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Default COGS for profit calculation"
    )
    # Tax rate stored as decimal fraction: 0.15 = 15%.
    # Applied at item level to avoid rounding errors on order totals.
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=4, default=0, help_text="e.g. 0.1500 = 15%"
    )

    # ── Physical attributes ────────────────────────────────────────────────
    sku = models.CharField(max_length=120, blank=True)
    weight_grams = models.PositiveIntegerField(
        null=True, blank=True, help_text="Master weight in grams"
    )
    length_cm = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    width_cm = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    height_cm = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    is_digital = models.BooleanField(default=False)

    # ── Arbitrary specifications (JSON) ────────────────────────────────────
    specifications = models.JSONField(default=dict, blank=True)

    # ── SEO ────────────────────────────────────────────────────────────────
    seo_title = models.CharField(max_length=120, blank=True)
    seo_description = models.TextField(max_length=320, blank=True)

    # ── Storefront ordering (manual drag-drop) ─────────────────────────────
    sort_order = models.PositiveIntegerField(default=0)

    # ── Full-text search vector (updated via signal) ───────────────────────
    search_vector = SearchVectorField(null=True, blank=True)
    
    # ── Semantic embedding for RAG (populated by Celery) ───────────────────
    # 1536-dim vector from text-embedding-3-small
    embedding = VectorField(dimensions=1536, null=True, blank=True)
    vector_status = models.CharField(
        max_length=20,
        choices=VectorStatus.choices,
        default=VectorStatus.PENDING,
        db_index=True,
    )

    class Meta:
        verbose_name = "Product"
        verbose_name_plural = "Products"
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="uq_product_shop_slug_active",
            ),
            models.UniqueConstraint(
                fields=["shop", "sku"],
                condition=models.Q(deleted_at__isnull=True, sku__gt=""),
                name="uq_product_shop_sku_active",
            ),
        ]
        indexes = [
            models.Index(
                fields=["shop", "status"],
                name="product_shop_status_idx",
                condition=models.Q(deleted_at__isnull=True),
            ),
            models.Index(
                fields=["shop", "sort_order"],
                name="product_shop_sort_idx",
                condition=models.Q(deleted_at__isnull=True),
            ),
            # Used for SCHEDULED auto-publish sweep
            models.Index(
                fields=["status", "publish_at"],
                name="product_scheduled_idx",
                condition=models.Q(
                    status="SCHEDULED", deleted_at__isnull=True
                ),
            ),
            # GIN index for full-text search
            models.Index(
                fields=["search_vector"],
                name="product_search_vector_gin_idx",
            ),
        ]

    def __str__(self):
        return f"{self.name} [{self.status}]"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    @property
    def total_stock(self) -> int:
        """Master stock = physical sum of all active variant stocks."""
        return (
            self.variants.filter(
                is_active=True, deleted_at__isnull=True
            ).aggregate(total=models.Sum("stock_quantity"))["total"]
            or 0
        )
