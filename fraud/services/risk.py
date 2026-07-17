from core.phone import hash_phone
from fraud.models import (
    FraudConfig,
    FraudProfile,
    FraudRiskLevel,
    FraudTargetType,
    GlobalFraudPool,
)
from users.models import PhoneIdentity

def check_customer_risk(shop, phone_number, actor_reference=None):
    """
    Checks the global fraud pool for a phone number and the local actor profile.
    Returns a risk summary.
    """
    # TODO: Implement OTP verification for all COD orders to minimize fraud. 
    # Currently skipped to minimize operating costs as per user request.

    risk_score = 0
    is_high_risk = False
    reports_count = 0
    details = {}

    config, _ = FraudConfig.objects.get_or_create(shop=shop)

    # 1. Check Actor Reference (IP/Messenger PSID)
    # Scope to actor target types only. actor_reference is overloaded (an IP for
    # web/API orders, a PSID for chat), so matching target_id alone could collide
    # with a PHONE/USER target that happens to share the string.
    if actor_reference:
        actor_profile = FraudProfile.objects.filter(
            target_type__in=(FraudTargetType.IP, FraudTargetType.FACEBOOK),
            target_id=actor_reference,
        ).only("risk_score", "risk_level").first()
        
        if actor_profile:
            risk_score = max(risk_score, actor_profile.risk_score)
            if actor_profile.risk_level == FraudRiskLevel.HIGH:
                is_high_risk = True
            reports_count += actor_profile.risk_score
            details["actor_risk"] = actor_profile.risk_score

    # 2. Check Phone Number
    if phone_number:
        # normalized_phone kept for the PhoneIdentity lookup below; the pool
        # hash now comes from the canonical phone module (E.164 sha256).
        normalized_phone = "".join(filter(str.isdigit, phone_number))
        phone_hash = hash_phone(phone_number)

        phone_identity = PhoneIdentity.objects.filter(phone_number=normalized_phone).only("id", "trust_score", "is_verified").first()
        
        if phone_identity:
            profile = FraudProfile.objects.filter(
                target_type=FraudTargetType.PHONE,
                target_id=str(phone_identity.id),
            ).only("risk_score", "risk_level").first()
            
            if profile:
                risk_score = max(risk_score, profile.risk_score)
                if profile.risk_level == FraudRiskLevel.HIGH:
                    is_high_risk = True
                reports_count += profile.risk_score
                details["phone_risk"] = profile.risk_score
                details["is_verified"] = phone_identity.is_verified

        # 3. Check Global Pool (if opted in)
        if config.opt_in_pooling:
            try:
                pool = GlobalFraudPool.objects.get(phone_hash=phone_hash)
                total_pool_reports = pool.rto_count + pool.fake_order_count + pool.harassment_count + pool.unpaid_count

                risk_score = max(risk_score, total_pool_reports)
                if pool.rto_count >= config.rto_threshold:
                    is_high_risk = True

                reports_count += total_pool_reports
                details["global_reports"] = total_pool_reports
            except GlobalFraudPool.DoesNotExist:
                pass

        # 4. Fold in neutral behavioral FACTS from the identity aggregate, read
        # ONLY through identity.services (never importing identity models — the
        # fact/policy seam, design doc §7). Facts are observations; the judgment
        # (turning an RTO rate into "high risk") is made here in fraud.
        from identity.services.behavioral import get_facts_by_phone

        facts = get_facts_by_phone(phone_number=phone_number)
        if facts:
            details["behavioral"] = facts
            delivered = facts["orders_delivered"]
            rto = facts["orders_rto"]
            decided = delivered + rto
            # An established RTO rate on a meaningful sample is a strong COD-risk
            # signal; a solid delivered history without RTO is trust-positive.
            if decided >= 3:
                rto_rate = rto / decided
                if rto_rate >= 0.5:
                    is_high_risk = True
                    details["behavioral_flag"] = "high_rto_rate"
            # Courier-reported delivery success (ingested observation), if present.
            if facts["courier_success_rate"] is not None and facts["courier_success_rate"] < 0.4:
                is_high_risk = True
                details["behavioral_flag"] = "low_courier_success"

    return {
        "risk_score": risk_score,
        "is_high_risk": is_high_risk or (risk_score >= config.rto_threshold),
        "reports_count": reports_count,
        "details": details,
    }
