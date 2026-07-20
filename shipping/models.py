import uuid
from django.db import models

from core.models import TenantModel

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


class CourierAccount(TenantModel):
    """
    Per-shop courier merchant credentials (e.g. a seller's own Pathao account).

    Credential blobs are JSON-encoded key-value pairs stored in
    ``credentials_encrypted``. Mirrors billing.PaymentGatewayConfig: stored as
    plain text in dev, swap to an EncryptedTextField before production.
    Never write ``credentials_encrypted`` directly — use
    shipping.services.set_courier_credentials().
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(
        'shops.Shop',
        on_delete=models.CASCADE,
        related_name='courier_accounts',
    )
    provider = models.CharField(max_length=20, choices=CourierProvider.choices)

    credentials_encrypted = models.TextField(
        blank=True,
        help_text=(
            "JSON-encoded courier credentials stored encrypted. "
            "Use shipping.services.set_courier_credentials() to write — never write directly."
        ),
    )

    # Provider-side store/warehouse id used as the pickup point when creating
    # consignments (e.g. Pathao store_id). Optional until the merchant creates a store.
    default_store_id = models.CharField(max_length=120, blank=True)

    label = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(
        default=True,
        help_text="Merchant can disable a courier without deleting its credentials.",
    )
    is_test_mode = models.BooleanField(
        default=False,
        help_text="Toggles the courier client to its sandbox environment.",
    )

    class Meta:
        db_table = 'shipping_courieraccount'
        constraints = [
            models.UniqueConstraint(
                fields=['shop', 'provider'],
                condition=models.Q(deleted_at__isnull=True),
                name='uq_courier_account_shop_provider',
            ),
        ]
        indexes = [
            models.Index(
                fields=['shop', 'is_active'],
                condition=models.Q(deleted_at__isnull=True),
                name='courier_acct_shop_active_idx',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_provider_display()} ({self.label or self.shop_id})"
