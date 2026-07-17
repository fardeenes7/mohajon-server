from django.db import models
import uuid

class GlobalFraudPool(models.Model):
    """
    Non-tenant model to store aggregated fraud data for cross-shop pooling.
    This bypasses RLS naturally since it's not a TenantModel.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone_hash = models.CharField(max_length=64, unique=True, db_index=True)
    hash_version = models.PositiveSmallIntegerField(default=0)

    rto_count = models.PositiveIntegerField(default=0)
    fake_order_count = models.PositiveIntegerField(default=0)
    harassment_count = models.PositiveIntegerField(default=0)
    unpaid_count = models.PositiveIntegerField(default=0)
    
    last_reported_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Pool: {self.phone_hash[:8]}... (RTO: {self.rto_count})"
