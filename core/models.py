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



