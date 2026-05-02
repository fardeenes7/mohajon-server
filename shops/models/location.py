from django.db import models
import uuid
from core.models import TenantModel

class LocationType(models.TextChoices):
    BRANCH = "BRANCH", "Retail Branch"
    WAREHOUSE = "WAREHOUSE", "Warehouse"
    OFFICE = "OFFICE", "Corporate Office"
    CUSTOMER_SERVICE = "CUSTOMER_SERVICE", "Customer Service Center"

class StockLocation(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="locations"
    )
    name = models.CharField(max_length=255)
    location_type = models.CharField(
        max_length=20,
        choices=LocationType.choices,
        default=LocationType.BRANCH
    )

    # Hierarchy: A warehouse can be controlled by a branch
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='children'
    )

    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    # Physical Details
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=20, blank=True)

    class Meta:
        verbose_name = "Stock Location"
        verbose_name_plural = "Stock Locations"
        constraints = [
            # Only one default location per shop
            models.UniqueConstraint(
                fields=['shop'],
                condition=models.Q(is_default=True, deleted_at__isnull=True),
                name='unique_default_location_per_shop'
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.get_location_type_display()})"

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)
