import hashlib

from fraud.models import FraudConfig, FraudProfile, FraudTargetType, GlobalFraudPool
from users.models import PhoneIdentity

def check_customer_risk(shop, phone_number):
    """
    Checks the global fraud pool for a phone number.
    Returns a risk summary.
    """
    if not phone_number:
        return {"risk_score": 0, "is_high_risk": False, "reports_count": 0}

    normalized_phone = "".join(filter(str.isdigit, phone_number))
    phone_hash = hashlib.sha256(normalized_phone.encode()).hexdigest()
    
    config, _ = FraudConfig.objects.get_or_create(shop=shop)
    
    # Opt-in check: You must contribute to see the global data
    if not config.opt_in_pooling:
        return {
            "risk_score": 0, 
            "is_high_risk": False, 
            "reports_count": 0,
            "message": "Opt-in to fraud pooling to see community data."
        }

    phone_identity = PhoneIdentity.objects.filter(phone_number=normalized_phone).only("id", "trust_score", "is_verified").first()
    if phone_identity:
        profile = FraudProfile.objects.filter(
            target_type=FraudTargetType.PHONE,
            target_id=str(phone_identity.id),
        ).only("risk_score").first()
        risk_score = profile.risk_score if profile else 0
        return {
            "risk_score": risk_score,
            "is_high_risk": risk_score >= config.rto_threshold,
            "reports_count": risk_score,
            "details": {
                "trust_score": phone_identity.trust_score,
                "is_verified": phone_identity.is_verified,
            },
        }

    try:
        pool = GlobalFraudPool.objects.get(phone_hash=phone_hash)
        total_reports = pool.rto_count + pool.fake_order_count + pool.harassment_count + pool.unpaid_count

        is_high_risk = pool.rto_count >= config.rto_threshold

        return {
            "risk_score": total_reports,
            "is_high_risk": is_high_risk,
            "reports_count": total_reports,
            "details": {
                "rto": pool.rto_count,
                "fake_order": pool.fake_order_count,
                "harassment": pool.harassment_count,
                "unpaid": pool.unpaid_count,
            },
        }
    except GlobalFraudPool.DoesNotExist:
        return {"risk_score": 0, "is_high_risk": False, "reports_count": 0}
