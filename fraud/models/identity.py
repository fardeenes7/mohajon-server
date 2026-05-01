import uuid

from django.conf import settings
from django.db import models


class FraudTargetType(models.TextChoices):
    PHONE = "PHONE", "Phone"
    USER = "USER", "User"
    FACEBOOK = "FACEBOOK", "Facebook"
    IP = "IP", "IP Address"


class FraudRiskLevel(models.TextChoices):
    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"


class FraudEventSource(models.TextChoices):
    ACTOR = "ACTOR", "Actor"
    PHONE = "PHONE", "Phone"


class FraudEventType(models.TextChoices):
    ORDER_CREATED = "ORDER_CREATED", "Order Created"
    ORDER_CANCELLED = "ORDER_CANCELLED", "Order Cancelled"
    DELIVERY_FAILED = "DELIVERY_FAILED", "Delivery Failed"
    FRAUD_REPORT_ADDED = "FRAUD_REPORT_ADDED", "Fraud Report Added"


class LinkReason(models.TextChoices):
    SHARED_PHONE = "SHARED_PHONE", "Shared Phone"
    SHARED_ADDRESS = "SHARED_ADDRESS", "Shared Address"
    SHARED_IP = "SHARED_IP", "Shared IP"


class FraudProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    target_type = models.CharField(max_length=20, choices=FraudTargetType.choices)
    target_id = models.CharField(max_length=255)
    phone_identity = models.ForeignKey(
        "users.PhoneIdentity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fraud_profiles",
    )
    risk_score = models.IntegerField(default=0, db_index=True)
    risk_level = models.CharField(max_length=10, choices=FraudRiskLevel.choices, default=FraudRiskLevel.LOW)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["target_type", "target_id"], name="uq_fraud_target"),
        ]
        indexes = [
            models.Index(fields=["target_type", "target_id"], name="fraud_target_idx"),
            models.Index(fields=["risk_score"], name="fraud_risk_score_idx"),
        ]


class FraudEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fraud_profile = models.ForeignKey(FraudProfile, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fraud_events",
    )
    phone_identity = models.ForeignKey(
        "users.PhoneIdentity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fraud_events",
    )
    event_type = models.CharField(max_length=30, choices=FraudEventType.choices)
    score_impact = models.IntegerField(default=0)
    confidence_level = models.CharField(max_length=10)
    source_type = models.CharField(max_length=10, choices=FraudEventSource.choices)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["fraud_profile", "created_at"], name="fraud_event_profile_time_idx"),
            models.Index(fields=["actor", "created_at"], name="fraud_event_actor_time_idx"),
            models.Index(fields=["phone_identity", "created_at"], name="fraud_event_phone_time_idx"),
        ]


class IdentityLink(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_a = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="identity_links_a")
    user_b = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="identity_links_b")
    link_reason = models.CharField(max_length=30, choices=LinkReason.choices)
    confidence_score = models.FloatField(default=1.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user_a", "user_b", "link_reason"], name="uq_identity_link"),
        ]
        indexes = [
            models.Index(fields=["user_a", "user_b"], name="identity_link_users_idx"),
        ]
