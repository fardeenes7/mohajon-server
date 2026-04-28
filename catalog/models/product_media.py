"""
ProductMedia — join table mapping media assets to products.

Handles ordered gallery images + thumbnail selection per product.
"""
import uuid

from django.db import models

from core.models import TenantModel


class ProductMedia(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="product_media",
    )
    media = models.ForeignKey(
        "mohajon_media.Media",
        on_delete=models.CASCADE,
        related_name="product_links",
    )
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="product_media_links",
        db_index=False,
        help_text="Denormalized for RLS efficiency",
    )
    sort_order = models.PositiveIntegerField(default=0)
    is_thumbnail = models.BooleanField(
        default=False,
        help_text="If True, this image is used as the product's primary thumbnail"
    )

    class Meta:
        verbose_name = "Product Media"
        verbose_name_plural = "Product Media"
        indexes = [
            models.Index(
                fields=["product", "sort_order"],
                name="productmedia_product_sort_idx",
                condition=models.Q(deleted_at__isnull=True),
            ),
        ]
        constraints = [
            # Only one thumbnail per product allowed
            models.UniqueConstraint(
                fields=["product"],
                condition=models.Q(is_thumbnail=True, deleted_at__isnull=True),
                name="uq_productmedia_one_thumbnail",
            )
        ]

    def __str__(self):
        return f"Media {self.media_id} → Product {self.product_id} [order={self.sort_order}]"
