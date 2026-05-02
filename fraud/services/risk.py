import hashlib

from fraud.models import FraudConfig, FraudProfile, FraudTargetType, GlobalFraudPool
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
    if actor_reference:
        actor_profile = FraudProfile.objects.filter(
            target_id=actor_reference
        ).only("risk_score", "risk_level").first()
        
        if actor_profile:
            risk_score = max(risk_score, actor_profile.risk_score)
            if actor_profile.risk_level == FraudRiskLevel.HIGH:
                is_high_risk = True
            reports_count += actor_profile.risk_score
            details["actor_risk"] = actor_profile.risk_score

    # 2. Check Phone Number
    if phone_number:
        normalized_phone = "".join(filter(str.isdigit, phone_number))
        phone_hash = hashlib.sha256(normalized_phone.encode()).hexdigest()

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

    return {
        "risk_score": risk_score,
        "is_high_risk": is_high_risk or (risk_score >= config.rto_threshold),
        "reports_count": reports_count,
        "details": details,
    }
