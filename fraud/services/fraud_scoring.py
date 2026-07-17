from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.db import transaction
from django.utils import timezone

from fraud.models import (
    FraudEvent,
    FraudEventSource,
    FraudEventType,
    FraudProfile,
    FraudRiskLevel,
    FraudTargetType,
)
from orders.models import OrderConfidenceLevel, OrderStatus
from users.models import PhoneIdentity


PROBATION_ORDER_COUNT = 3
PROBATION_MULTIPLIER = 0.5
RISK_LEVEL_THRESHOLDS = {
    FraudRiskLevel.LOW: 10,
    FraudRiskLevel.MEDIUM: 30,
}
ABUSE_UNVERIFIED_THRESHOLD = 3


@dataclass
class ActorTarget:
    target_type: str
    target_id: str
    actor_id: Optional[str]
    actor_reference: Optional[str]


def get_actor_target(order) -> ActorTarget:
    if order.user_id:
        return ActorTarget(
            target_type=FraudTargetType.USER,
            target_id=str(order.user_id),
            actor_id=str(order.user_id),
            actor_reference=None,
        )
    return ActorTarget(
        target_type=FraudTargetType.IP,
        target_id=order.actor_reference or "",
        actor_id=None,
        actor_reference=order.actor_reference,
    )


def _get_or_create_profile(target_type: str, target_id: str, phone_identity: PhoneIdentity | None = None) -> FraudProfile:
    profile, _ = FraudProfile.objects.get_or_create(
        target_type=target_type,
        target_id=target_id,
        defaults={"phone_identity": phone_identity},
    )
    if phone_identity and profile.phone_identity_id != phone_identity.id:
        profile.phone_identity = phone_identity
        profile.save(update_fields=["phone_identity"])
    return profile


def _record_event(
    *,
    profile: FraudProfile,
    actor_id: str | None,
    phone_identity_id: str | None,
    event_type: str,
    score_impact: int,
    confidence_level: str,
    source_type: str,
    metadata: dict,
) -> None:
    FraudEvent.objects.create(
        fraud_profile=profile,
        actor_id=actor_id,
        phone_identity_id=phone_identity_id,
        event_type=event_type,
        score_impact=score_impact,
        confidence_level=confidence_level,
        source_type=source_type,
        metadata=metadata,
    )


def calculate_order_confidence(order) -> str:
    # TODO: Once OTP for COD is implemented to minimize operating costs, 
    # require OTP for all HIGH confidence levels unless manually bypassed by staff.

    if order.is_verified and order.phone_identity_id:
        phone_identity = PhoneIdentity.objects.filter(id=order.phone_identity_id).only("trust_score", "user_id", "is_verified").first()
    else:
        phone_identity = None

    actor_profile = None
    actor_target = get_actor_target(order)
    if actor_target.target_id:
        actor_profile = FraudProfile.objects.filter(
            target_type=actor_target.target_type,
            target_id=actor_target.target_id,
        ).only("risk_score").first()

    # 1. High Risk Block
    if actor_profile and actor_profile.risk_score > 50:
        return OrderConfidenceLevel.LOW

    # 2. WhatsApp Auto-Verification
    # If the actor_reference (phone number) matches the shipping phone, it's auto-verified.
    from orders.models import VerificationMethod
    if not order.is_verified and order.actor_reference and order.phone_identity_id:
        actor_digits = "".join(filter(str.isdigit, order.actor_reference))
        # PhoneIdentity.phone_number is already normalized to digits in its save() method
        phone_identity_obj = phone_identity or PhoneIdentity.objects.filter(id=order.phone_identity_id).only("phone_number").first()
        
        if actor_digits and phone_identity_obj and actor_digits == phone_identity_obj.phone_number:
            order.is_verified = True
            order.verification_method = VerificationMethod.SOCIAL

    # 3. Verification Boost
    
    if order.is_verified:
        # OTP and WhatsApp/Social match are strongest
        if order.verification_method in {VerificationMethod.OTP, VerificationMethod.SOCIAL}:
            return OrderConfidenceLevel.HIGH
        
        # Phone call verification (Manual/Messenger/WhatsApp)
        if order.verification_method == VerificationMethod.CALL:
            if phone_identity and phone_identity.trust_score >= 0:
                return OrderConfidenceLevel.MEDIUM
            return OrderConfidenceLevel.LOW

    # 3. Trust Score Logic (for unverified but potentially good customers)
    if phone_identity and phone_identity.is_verified and phone_identity.trust_score > 0:
        if order.user_id and phone_identity.user_id == order.user_id:
            return OrderConfidenceLevel.HIGH
        return OrderConfidenceLevel.MEDIUM

    # 4. Default for Social/Manual (Medium if no prior risk, else Low)
    if not order.is_verified:
        if not actor_profile or actor_profile.risk_score == 0:
            return OrderConfidenceLevel.MEDIUM
        return OrderConfidenceLevel.LOW

    return OrderConfidenceLevel.MEDIUM


def apply_fraud_penalty(order, *, event_type: str, base_penalty: int) -> None:
    actor_target = get_actor_target(order)

    with transaction.atomic():
        actor_profile = _get_or_create_profile(actor_target.target_type, actor_target.target_id)
        actor_profile.risk_score += base_penalty
        actor_profile.save(update_fields=["risk_score", "updated_at"])

        _record_event(
            profile=actor_profile,
            actor_id=actor_target.actor_id,
            phone_identity_id=order.phone_identity_id,
            event_type=event_type,
            score_impact=base_penalty,
            confidence_level=order.confidence_level,
            source_type=FraudEventSource.ACTOR,
            metadata={
                "order_id": str(order.id),
                "actor_reference": actor_target.actor_reference,
            },
        )

        if not order.is_verified or order.confidence_level == OrderConfidenceLevel.LOW:
            return

        if not order.phone_identity_id:
            return

        completed_orders = OrderStatus.DELIVERED
        delivered_count = (
            order.__class__.objects.filter(phone_identity_id=order.phone_identity_id, status=completed_orders).count()
        )
        multiplier = PROBATION_MULTIPLIER if delivered_count < PROBATION_ORDER_COUNT else 1.0
        phone_penalty = int(base_penalty * multiplier)

        phone_profile = _get_or_create_profile(
            FraudTargetType.PHONE,
            str(order.phone_identity_id),
            phone_identity=order.phone_identity,
        )
        phone_profile.risk_score += phone_penalty
        phone_profile.save(update_fields=["risk_score", "updated_at"])

        _record_event(
            profile=phone_profile,
            actor_id=actor_target.actor_id,
            phone_identity_id=order.phone_identity_id,
            event_type=event_type,
            score_impact=phone_penalty,
            confidence_level=order.confidence_level,
            source_type=FraudEventSource.PHONE,
            metadata={
                "order_id": str(order.id),
                "actor_reference": actor_target.actor_reference,
                "verified_at": timezone.now().isoformat(),
            },
        )


def dispatch_fraud_event(order, event_type: str, base_penalty: int = 0, metadata: dict | None = None) -> None:
    event_metadata = metadata or {}

    if event_type == FraudEventType.ORDER_CREATED:
        actor_target = get_actor_target(order)
        actor_profile = _get_or_create_profile(actor_target.target_type, actor_target.target_id)
        _record_event(
            profile=actor_profile,
            actor_id=actor_target.actor_id,
            phone_identity_id=order.phone_identity_id,
            event_type=event_type,
            score_impact=0,
            confidence_level=order.confidence_level,
            source_type=FraudEventSource.ACTOR,
            metadata={
                "order_id": str(order.id),
                "actor_reference": actor_target.actor_reference,
                "context": event_metadata,
            },
        )
        if actor_target.actor_reference:
            evaluate_abuse_patterns(actor_target.actor_reference)
        return

    if event_type in {FraudEventType.ORDER_CANCELLED, FraudEventType.DELIVERY_FAILED, FraudEventType.FRAUD_REPORT_ADDED}:
        apply_fraud_penalty(order, event_type=event_type, base_penalty=base_penalty)


def evaluate_abuse_patterns(actor_reference: str) -> None:
    if not actor_reference:
        return

    since = timezone.now() - timedelta(hours=24)
    suspicious = (
        PhoneIdentity.objects.filter(orders__actor_reference=actor_reference, orders__created_at__gte=since)
        .filter(orders__is_verified=False)
        .values("id")
        .distinct()
        .count()
    )

    if suspicious < ABUSE_UNVERIFIED_THRESHOLD:
        return

    profile = _get_or_create_profile(FraudTargetType.IP, actor_reference)
    profile.risk_score = max(profile.risk_score, RISK_LEVEL_THRESHOLDS[FraudRiskLevel.MEDIUM])
    profile.risk_level = FraudRiskLevel.HIGH
    profile.save(update_fields=["risk_score", "risk_level", "updated_at"])
