import uuid
from django.db import models

class CourierProvider(models.TextChoices):
    PATHAO = 'PATHAO', 'Pathao'
    PAPERFLY = 'PAPERFLY', 'Paperfly'
    STEADFAST = 'STEADFAST', 'Steadfast'
    OTHER = 'OTHER', 'Other'

class CourierConsignmentStatus(models.TextChoices):
    CREATED = 'CREATED', 'Created'
    DISPATCHED = 'DISPATCHED', 'Dispatched'
    IN_TRANSIT = 'IN_TRANSIT', 'In Transit'
    DELIVERED = 'DELIVERED', 'Delivered'
    FAILED = 'FAILED', 'Failed'
    RTO = 'RTO', 'Return To Origin'

class CourierConsignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey('orders.Order', on_delete=models.CASCADE, related_name='consignments')
    shop = models.ForeignKey('shops.Shop', on_delete=models.CASCADE, related_name='courier_consignments')
    provider = models.CharField(max_length=20, choices=CourierProvider.choices, default=CourierProvider.OTHER)
    external_consignment_id = models.CharField(max_length=120)
    tracking_code = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=CourierConsignmentStatus.choices, default=CourierConsignmentStatus.CREATED)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'orders_courierconsignment'
        indexes = [
            models.Index(fields=['shop', 'status', 'created_at'], name='courier_shop_stat_cr_ix'),
            models.Index(fields=['provider', 'external_consignment_id'], name='courier_prov_ext_idx'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['provider', 'external_consignment_id'], name='uq_courier_provider_extid'),
        ]
