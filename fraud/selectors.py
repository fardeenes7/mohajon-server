from __future__ import annotations

from fraud.models import FraudConfig

def get_fraud_config(shop_id: str) -> FraudConfig:
    # returns fraud config, or creates a default one
    config, _ = FraudConfig.objects.get_or_create(shop_id=shop_id)
    return config
