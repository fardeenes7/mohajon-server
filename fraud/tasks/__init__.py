"""
Fraud policy tasks (design doc Phase 4.2).

Two periodic sweeps that keep risk scores honest over time:

  * decay_risk_scores  — risk is a fading signal, not a life sentence. A phone
    that misbehaved once and then went quiet should drift back toward neutral so
    a shop is not permanently blocked by a stale strike. Scores decay toward 0.

  * refresh_trust_scores — the inverse: a phone with a clean, delivered history
    earns a positive trust_score (read all over the codebase, never written
    until now). Computed from the neutral behavioral aggregate read through
    identity.services — fraud never imports identity models.

Both are idempotent and safe to re-run; they read facts and rewrite derived
policy numbers, so a missed or doubled run only changes timing, not correctness.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# Risk points shed per decay run for profiles with no recent negative event.
RISK_DECAY_STEP = 5
# A profile is eligible to decay only after this long without a new event.
RISK_DECAY_IDLE_DAYS = 14

# Trust ceiling and the delivered-order count needed to reach it.
TRUST_SCORE_MAX = 100
TRUST_PER_DELIVERED = 10


@shared_task(name="fraud.tasks.decay_risk_scores")
def decay_risk_scores() -> dict:
    """
    Shed RISK_DECAY_STEP from every FraudProfile whose most recent event is older
    than RISK_DECAY_IDLE_DAYS, flooring at 0. Recompute risk_level from the
    decayed score so a cooled-off profile stops reading as high risk.
    """
    from fraud.models import FraudProfile, FraudRiskLevel
    from fraud.services.fraud_scoring import RISK_LEVEL_THRESHOLDS

    cutoff = timezone.now() - timedelta(days=RISK_DECAY_IDLE_DAYS)

    stale = (
        FraudProfile.objects.filter(risk_score__gt=0)
        .exclude(events__created_at__gte=cutoff)
        .only("id", "risk_score")
    )

    decayed = 0
    for profile in stale.iterator():
        new_score = max(0, profile.risk_score - RISK_DECAY_STEP)
        if new_score >= RISK_LEVEL_THRESHOLDS[FraudRiskLevel.MEDIUM]:
            level = FraudRiskLevel.HIGH
        elif new_score >= RISK_LEVEL_THRESHOLDS[FraudRiskLevel.LOW]:
            level = FraudRiskLevel.MEDIUM
        else:
            level = FraudRiskLevel.LOW
        FraudProfile.objects.filter(pk=profile.pk).update(
            risk_score=new_score, risk_level=level
        )
        decayed += 1

    logger.info("decay_risk_scores: decayed %s profiles", decayed)
    return {"decayed": decayed}


@shared_task(name="fraud.tasks.refresh_trust_scores")
def refresh_trust_scores() -> dict:
    """
    Recompute trust_score for verified PhoneIdentities from their delivered
    history (read via identity.services — the fact/policy seam). Clean delivered
    orders raise trust; any RTO holds it back. Bounded to TRUST_SCORE_MAX.
    """
    from identity.services.behavioral import get_facts_by_phone
    from users.models import PhoneIdentity

    updated = 0
    for identity in PhoneIdentity.objects.filter(is_verified=True).only(
        "id", "phone_number", "trust_score"
    ).iterator():
        facts = get_facts_by_phone(phone_number=identity.phone_number)
        if not facts:
            continue
        # Trust rewards delivered orders and is dragged down by RTOs.
        net = facts["orders_delivered"] - facts["orders_rto"]
        new_trust = max(0, min(TRUST_SCORE_MAX, net * TRUST_PER_DELIVERED))
        if new_trust != identity.trust_score:
            PhoneIdentity.objects.filter(pk=identity.pk).update(trust_score=new_trust)
            updated += 1

    logger.info("refresh_trust_scores: updated %s identities", updated)
    return {"updated": updated}
