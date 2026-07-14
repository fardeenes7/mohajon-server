from __future__ import annotations

from decimal import Decimal
import logging
from django.db import transaction

logger = logging.getLogger(__name__)

# 1 credit = $0.01 USD actual API cost (global_business_rules_and_limits.md §3)
USD_PER_CREDIT = Decimal("0.01")


def calculate_credits(
    *, 
    model_input_rate: Decimal, 
    model_output_rate: Decimal, 
    input_tokens: int, 
    output_tokens: int
) -> tuple[Decimal, Decimal]:
    """
    Calculate credit cost based on token usage and model rates.
    Rates are USD per 1M tokens.
    Returns (credits, usd_cost).
    """
    usd_cost = (
        model_input_rate * input_tokens / 1_000_000
        + model_output_rate * output_tokens / 1_000_000
    )
    credits = (usd_cost / USD_PER_CREDIT).quantize(Decimal("0.01"))
    return credits, usd_cost


def deduct_ai_credits(*, shop_id: str, credits: Decimal) -> None:
    """Atomic credit deduction from ShopSettings."""
    from shops.models import ShopSettings
    
    if credits <= 0:
        return

    with transaction.atomic():
        try:
            settings_obj = ShopSettings.objects.select_for_update().get(
                shop_id=shop_id, deleted_at__isnull=True
            )
            settings_obj.ai_credit_balance = max(Decimal("0"), settings_obj.ai_credit_balance - credits)
            settings_obj.save(update_fields=["ai_credit_balance", "updated_at"])
        except ShopSettings.DoesNotExist:
            logger.error("ShopSettings not found for shop_id=%s during credit deduction", shop_id)


def has_sufficient_ai_credits(*, shop_id: str, minimum: Decimal = Decimal("0.01")) -> bool:
    """Check if a shop has enough credits for a baseline AI call."""
    from shops.models import ShopSettings
    try:
        settings_obj = ShopSettings.objects.get(shop_id=shop_id, deleted_at__isnull=True)
        return settings_obj.ai_credit_balance >= minimum
    except ShopSettings.DoesNotExist:
        return False
