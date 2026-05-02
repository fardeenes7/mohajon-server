"""
ProductVariant model.

One variant = one buyable combination (e.g. "Red / M" or "Blue / XL").
- Max 2 attribute levels enforced at the service layer.
- Max 25 variants per product enforced at the service layer.
- Variants CAN override price and weight from the master product.
- Stock is tracked per variant; master product stock is computed sum.
"""
import uuid

from django.db import models

from core.models import TenantModel


class ProductVariant(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="variants",
    )
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="product_variants",
        db_index=False,  # covered by compound index
        help_text="Denormalized for RLS index efficiency",
    )
    image = models.ForeignKey(
        "mohajon_media.Media",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="variant_images",
    )

    # ── Attributes (max 2 levels per business rules) ───────────────────────
    attribute_name_1 = models.CharField(max_length=50, blank=True)
    attribute_value_1 = models.CharField(max_length=100, blank=True)
    attribute_name_2 = models.CharField(max_length=50, blank=True)
    attribute_value_2 = models.CharField(max_length=100, blank=True)

    # ── Per-variant identity ───────────────────────────────────────────────
    sku = models.CharField(max_length=120, blank=True)

    # ── Optional overrides ─────────────────────────────────────────────────
    price_override = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Overrides master product price if set"
    )
    weight_override_grams = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Overrides master product weight if set"
    )
    purchase_price_override = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Overrides master purchase_price if set"
    )

    # ── Inventory ──────────────────────────────────────────────────────────
    # TODO: Refactor into StockRecord entirely after MVP launch.
    # Currently, this field acts as 'Global' stock for the MVP storefront.
    # New StockRecord model should be used for location-specific queries.
    stock_quantity = models.PositiveIntegerField(
        default=0,
        help_text="Global/Legacy stock. Use StockRecord for multi-location tracking."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Product Variant"
        verbose_name_plural = "Product Variants"
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "sku"],
                condition=models.Q(deleted_at__isnull=True, sku__gt=""),
                name="uq_variant_shop_sku_active",
            )
        ]
        indexes = [
            models.Index(
                fields=["shop", "product"],
                name="variant_shop_product_idx",
                condition=models.Q(deleted_at__isnull=True),
            ),
            models.Index(
                fields=["shop", "sku"],
                name="variant_shop_sku_idx",
                condition=models.Q(deleted_at__isnull=True, sku__gt=""),
            ),
        ]

    def __str__(self):
        attrs = " / ".join(
            filter(None, [self.attribute_value_1, self.attribute_value_2])
        )
        return f"{self.product.name} - {attrs or 'Default'} (SKU: {self.sku})"

    @property
    def effective_price(self):
        """Returns variant price if overridden, else master product base_price."""
        return self.price_override if self.price_override is not None else self.product.base_price

    @property
    def effective_weight(self):
        return (
            self.weight_override_grams
            if self.weight_override_grams is not None
            else self.product.weight_grams
        )

    @property
    def effective_purchase_price(self):
        """Returns variant purchase_price if overridden, else master product purchase_price."""
        return self.purchase_price_override if self.purchase_price_override is not None else self.product.purchase_price

    def get_stock_at_location(self, location_id):
        """Returns stock quantity for a specific location."""
        return self.stock_records.filter(location_id=location_id).values_list('quantity', flat=True).first() or 0

    @property
    def global_stock(self):
        """
        Sum of stock across ALL locations.
        Used by POS mode 'View Global Stock' regardless of user assignment.
        """
        return self.stock_records.aggregate(total=models.Sum('quantity'))['total'] or 0
