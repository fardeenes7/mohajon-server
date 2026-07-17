import uuid

from django.db import models

from core.models import TenantModel
from core.phone import HASH_VERSION, hash_phone

class FraudReport(TenantModel):
    """
    Merchant-reported fraudulent phone number or customer.
    Hashed phone number is used for cross-platform pooling.
    """
    REASON_CHOICES = (
        ('RTO', 'Return To Origin (RTO)'),
        ('FAKE_ORDER', 'Fake Order / Prank'),
        ('HARASSMENT', 'Harassment'),
        ('UNPAID', 'Unpaid Advanced Fee'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey('shops.Shop', on_delete=models.CASCADE, related_name='fraud_reports')
    merchant_id = models.UUIDField(null=True, blank=True)
    order = models.ForeignKey('orders.Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='fraud_reports')
    phone_identity = models.ForeignKey('users.PhoneIdentity', on_delete=models.SET_NULL, null=True, blank=True)
    
    # PII (Only visible to the reporting shop via RLS or specific logic)
    phone_number = models.CharField(max_length=20)
    customer_name = models.CharField(max_length=255, blank=True)
    
    # Hashed PII (For cross-platform pooling)
    phone_hash = models.CharField(max_length=64, db_index=True)
    hash_version = models.PositiveSmallIntegerField(default=0)
    
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    weight = models.IntegerField(default=10)
    notes = models.TextField(blank=True)

    # Misuse protection (design doc Phase 4.3): a report only contributes to the
    # cross-shop GlobalFraudPool once it is validated — i.e. tied to a genuine
    # order between the reporting shop and the target, and within rate limits.
    # The pool signal gates on this flag so a shop cannot poison a phone's pooled
    # score without a real transaction. The report row is still stored either way
    # (the reporting shop sees its own reports via RLS regardless).
    counts_toward_pool = models.BooleanField(default=False)

    reported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['phone_hash'], name='fraud_phone_hash_idx'),
            models.Index(fields=['shop', 'reported_at'], name='fraud_shop_report_idx'),
            models.Index(fields=['reported_at'], name='fraud_reported_at_idx'),
        ]

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        
        # Generate hash for pooling via the canonical phone module (E.164 sha256).
        # Keep the raw phone_number as-is: it's PII kept for the reporting shop.
        self.phone_hash = hash_phone(self.phone_number)
        self.hash_version = HASH_VERSION

        # PhoneIdentity backfill lookup — left as-is; another agent owns
        # PhoneIdentity normalization.
        normalized_phone = "".join(filter(str.isdigit, self.phone_number))
        if not self.phone_identity_id and normalized_phone:
            from users.models import PhoneIdentity

            self.phone_identity = PhoneIdentity.objects.filter(phone_number=normalized_phone).first()
        
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.reason} - {self.phone_number}"
