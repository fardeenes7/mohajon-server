import hashlib
import uuid

from django.db import models

from core.models import TenantModel

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
    
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    weight = models.IntegerField(default=10)
    notes = models.TextField(blank=True)
    
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
        
        # Generate hash for pooling (sha256 of normalized phone)
        normalized_phone = "".join(filter(str.isdigit, self.phone_number))
        self.phone_hash = hashlib.sha256(normalized_phone.encode()).hexdigest()
        if not self.phone_identity_id and normalized_phone:
            from users.models import PhoneIdentity

            self.phone_identity = PhoneIdentity.objects.filter(phone_number=normalized_phone).first()
        
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.reason} - {self.phone_number}"
