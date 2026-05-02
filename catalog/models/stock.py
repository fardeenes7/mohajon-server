from django.db import models
import uuid
from core.models import TenantModel

class StockRecord(TenantModel):
    """
    Physical stock level for a specific variant at a specific location.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="stock_records"
    )
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.CASCADE,
        related_name="stock_records"
    )
    location = models.ForeignKey(
        "shops.StockLocation",
        on_delete=models.CASCADE,
        related_name="stock_records"
    )
    
    quantity = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Stock Record"
        verbose_name_plural = "Stock Records"
        constraints = [
            models.UniqueConstraint(
                fields=['variant', 'location'],
                condition=models.Q(deleted_at__isnull=True),
                name='uq_variant_per_location'
            )
        ]
        indexes = [
            models.Index(fields=['shop', 'location'], name='stock_shop_loc_idx'),
        ]

    def __str__(self):
        return f"{self.variant.sku} @ {self.location.name}: {self.quantity}"

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)
