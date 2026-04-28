import uuid
from django.db import models
from django.conf import settings


class Media(models.Model):
    """
    Represents a single uploaded media asset for a shop.
    Physical S3 deletion is deferred to the Celery orphan-cleanup task;
    this record is soft-deleted by setting deleted_at.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="media_assets",
        db_index=False,  # covered by compound index below
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_media",
    )

    # S3 / storage fields
    original_filename = models.CharField(max_length=512)
    s3_key = models.CharField(max_length=1024, unique=True, db_index=True)
    cdn_url = models.URLField(max_length=2048, blank=True)
    md5_hash = models.CharField(max_length=32, blank=True, db_index=True)
    file_size = models.PositiveBigIntegerField(null=True, blank=True)  # bytes
    mime_type = models.CharField(max_length=127, blank=True)

    # Image dimensions (populated after WebP conversion)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)

    # Processing state
    class ProcessingStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        DONE = "DONE", "Done"
        FAILED = "FAILED", "Failed"

    processing_status = models.CharField(
        max_length=15,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.PENDING,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        app_label = "mohajon_media"
        verbose_name = "Media"
        verbose_name_plural = "Media"
        indexes = [
            # Primary query pattern: shop's non-deleted assets ordered by creation
            models.Index(
                fields=["shop", "created_at"],
                name="media_shop_created_idx",
                condition=models.Q(deleted_at__isnull=True),
            ),
            # For orphan cleanup: find all non-deleted records quickly
            models.Index(
                fields=["s3_key"],
                name="media_s3key_active_idx",
                condition=models.Q(deleted_at__isnull=True),
            ),
        ]

    def __str__(self):
        return f"{self.original_filename} ({self.shop_id})"

    def soft_delete(self):
        """Mark as deleted — physical S3 purge handled by Celery beat task."""
        from django.utils import timezone
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
