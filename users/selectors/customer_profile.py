from django.db import models
from django.db.models import Q

from fraud.models import FraudEvent, FraudProfile, FraudTargetType
from orders.models import Order
from users.models import PhoneIdentity, User


def get_customer_profile(user_id: str):
    user = (
        User.objects.prefetch_related(
            "addresses",
            "social_accounts",
            "phone_identities",
        )
        .filter(id=user_id)
        .first()
    )
    if not user:
        return None

    primary_phone = user.phone_identities.filter(is_verified=True).first() or user.phone_identities.first()

    user_profile = FraudProfile.objects.filter(
        target_type=FraudTargetType.USER,
        target_id=str(user.id),
    ).only("risk_score", "risk_level").first()

    phone_profile = None
    if primary_phone:
        phone_profile = FraudProfile.objects.filter(
            target_type=FraudTargetType.PHONE,
            target_id=str(primary_phone.id),
        ).only("risk_score", "risk_level").first()

    target_ids = [str(user.id)]
    if primary_phone:
        target_ids.append(str(primary_phone.id))

    recent_events = (
        FraudEvent.objects.filter(
            fraud_profile__target_type__in=[FraudTargetType.USER, FraudTargetType.PHONE],
            fraud_profile__target_id__in=target_ids,
        )
        .order_by("-created_at")
        .select_related("fraud_profile")[:20]
    )

    order_stats = Order.objects.filter(user_id=user.id).aggregate(
        total=models.Count("id"),
        delivered=models.Count("id", filter=Q(status="DELIVERED")),
        cancelled=models.Count("id", filter=Q(status="CANCELLED")),
    )

    return {
        "user": user,
        "primary_phone": primary_phone,
        "user_profile": user_profile,
        "phone_profile": phone_profile,
        "recent_events": recent_events,
        "order_stats": order_stats,
    }


def find_customer_by_phone(phone: str):
    normalized = "".join(filter(str.isdigit, phone or ""))
    identity = PhoneIdentity.objects.filter(phone_number=normalized).select_related("user").first()

    if not identity and len(normalized) >= 4:
        suffix = normalized[-4:]
        identity = PhoneIdentity.objects.filter(
            phone_suffix=suffix,
            phone_number__endswith=normalized,
        ).select_related("user").first()

    return identity
