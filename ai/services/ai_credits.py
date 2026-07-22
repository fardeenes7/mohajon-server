from __future__ import annotations

from decimal import Decimal
import logging

from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

logger = logging.getLogger(__name__)

# 1 credit = $0.01 USD actual API cost (global_business_rules_and_limits.md §3)
USD_PER_CREDIT = Decimal("0.01")

# Precision for stored credit balances (matches AICreditLot decimal_places).
_CREDIT_QUANT = Decimal("0.01")


def calculate_credits(
    *,
    model_input_rate: Decimal,
    model_output_rate: Decimal,
    input_tokens: int,
    output_tokens: int,
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


def _active_lots_qs(shop_id: str):
    """
    Lots that currently contribute to a shop's usable balance: not expired,
    not exhausted, and either non-expiring or not yet past expiry.
    """
    from ai.models import AICreditLot

    now = timezone.now()
    return AICreditLot.objects.filter(
        shop_id=shop_id,
        deleted_at__isnull=True,
        is_expired=False,
        is_exhausted=False,
        credits_remaining__gt=0,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))


def available_credits(*, shop_id: str) -> Decimal:
    """Sum of spendable credits across all active (non-expired) lots."""
    total = _active_lots_qs(shop_id).aggregate(total=Sum("credits_remaining"))["total"]
    return (total or Decimal("0")).quantize(_CREDIT_QUANT)


def _sync_cached_balance(shop_id: str) -> Decimal:
    """
    Reconcile the flat ``ShopSettings.ai_credit_balance`` cache with the
    ledger's true available balance so existing readers stay correct.
    """
    from shops.models import ShopSettings

    balance = available_credits(shop_id=shop_id)
    updated = ShopSettings.objects.filter(
        shop_id=shop_id, deleted_at__isnull=True
    ).update(ai_credit_balance=balance, updated_at=timezone.now())
    if not updated:
        logger.error(
            "ShopSettings not found for shop_id=%s during balance sync", shop_id
        )
    return balance


def grant_ai_credits(
    *,
    shop_id: str,
    credits: Decimal,
    category: str,
    expires_at=None,
    note: str = "",
    created_by=None,
    source_topup=None,
):
    """
    Grant a new lot of AI credits to a shop and sync the cached balance.

    This is the single entry point for ALL credit grants — purchases,
    referral/promotion bonuses, and manual admin adjustments.
    Returns the created AICreditLot (or None on invalid input).
    """
    from ai.models import AICreditLot

    credits = Decimal(str(credits)).quantize(_CREDIT_QUANT)
    if credits <= 0:
        logger.warning("grant_ai_credits called with non-positive credits=%s", credits)
        return None

    with transaction.atomic():
        lot = AICreditLot.objects.create(
            shop_id=shop_id,
            tenant_id=shop_id,
            category=category,
            credits_granted=credits,
            credits_remaining=credits,
            expires_at=expires_at,
            note=note,
            created_by=created_by,
            source_topup=source_topup,
        )
        _sync_cached_balance(shop_id)
    return lot


def deduct_ai_credits(*, shop_id: str, credits: Decimal) -> Decimal:
    """
    Draw credits from a shop's lots FIFO by soonest expiry (non-expiring lots
    last). Returns the amount actually deducted.

    If the shop has fewer credits than requested we draw everything available
    and floor at zero (preserving the previous forgiving behaviour), logging a
    warning about the shortfall.
    """
    credits = Decimal(str(credits)).quantize(_CREDIT_QUANT)
    if credits <= 0:
        return Decimal("0")

    with transaction.atomic():
        # Soonest expiry first; non-expiring (null) lots drawn last.
        lots = list(
            _active_lots_qs(shop_id)
            .select_for_update()
            .order_by(F("expires_at").asc(nulls_last=True), "created_at")
        )

        remaining = credits
        for lot in lots:
            if remaining <= 0:
                break
            take = min(lot.credits_remaining, remaining)
            lot.credits_remaining -= take
            lot.is_exhausted = lot.credits_remaining <= 0
            lot.save(update_fields=["credits_remaining", "is_exhausted", "updated_at"])
            remaining -= take

        _sync_cached_balance(shop_id)

    deducted = (credits - remaining).quantize(_CREDIT_QUANT)
    if remaining > 0:
        logger.warning(
            "AI credit shortfall for shop_id=%s: requested=%s deducted=%s",
            shop_id,
            credits,
            deducted,
        )
    return deducted


def has_sufficient_ai_credits(*, shop_id: str, minimum: Decimal = Decimal("0.01")) -> bool:
    """Check if a shop has enough spendable (non-expired) credits."""
    return available_credits(shop_id=shop_id) >= minimum
