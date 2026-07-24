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
    """Check if a shop has enough spendable (non-expired) credits.

    Accounts for credits currently reserved by in-flight AI turns so
    concurrent requests don't both pass the check and collectively overspend.
    """
    balance = available_credits(shop_id=shop_id)
    reserved = _get_total_reserved(shop_id)
    return (balance - reserved) >= minimum


# ---------------------------------------------------------------------------
# Credit reservation system — prevents quota race conditions across
# concurrent AI turns for the same tenant.
#
# How it works (estimate-then-reconcile):
#   1. Before entering the tool-call loop, `reserve_ai_credits` atomically
#      checks that (balance - already_reserved) >= estimated_cost, then
#      increments the reservation counter in Redis.
#   2. After the loop, `reconcile_ai_credits` releases the reservation and
#      lets `log_accumulated_usage` / `deduct_ai_credits` charge the real
#      amount against the DB ledger.
#   3. If the task crashes mid-loop, `release_ai_credits` in a `finally`
#      block frees the reservation so credits aren't permanently locked.
#
# The Redis counter is an *optimistic limiter*, not the source of truth.
# The DB ledger (AICreditLot) remains authoritative. A brief window where
# balance appears as "reserved-but-unspent" is acceptable — it self-corrects
# the moment reconciliation runs.
# ---------------------------------------------------------------------------

# Max consecutive function calls per AI turn (mirrors engine._MAX_TOOL_CALLS)
_RESERVATION_MAX_TOOL_CALLS = 5

# Conservative average tokens per tool-call iteration (prompt + completion).
# This overestimates intentionally — unused reservation is released after the
# turn. Adjust if real-world usage shows persistent over-reservation.
_AVG_TOKENS_PER_ITERATION = 4000


def _reservation_key(shop_id: str) -> str:
    """Redis key tracking total in-flight credit reservations for a shop."""
    return f"credits_reserved:{shop_id}"


def _get_total_reserved(shop_id: str) -> Decimal:
    """Read the current total reserved credits from Redis (non-blocking)."""
    try:
        from django_redis import get_redis_connection
        r = get_redis_connection("default")
        raw = r.get(_reservation_key(shop_id))
        if raw is None:
            return Decimal("0")
        # Stored as integer hundredths of credits (e.g. 150 = 1.50 credits)
        return (Decimal(int(raw)) / 100).quantize(_CREDIT_QUANT)
    except Exception:
        # Redis down — return 0 so the pre-check doesn't block everything.
        return Decimal("0")


def estimate_turn_credits(*, shop_id: str) -> Decimal:
    """
    Estimate a conservative credit ceiling for one AI turn.

    Formula:
        MAX_TOOL_CALLS × avg_tokens_per_iteration × (input_rate + output_rate) / 1M / USD_PER_CREDIT

    This is an approximation, not exact accounting. It intentionally over-
    estimates so the reservation acts as a safe ceiling. The unused portion
    is released after the turn completes.

    TODO: Future improvement — make this tenant-specific based on historical
    average usage per turn, rather than a global constant.
    """
    from ai.services.ai_model_registry import resolve_ai_model
    from ai.models import AIModelUsage

    model = resolve_ai_model(usage=AIModelUsage.CHAT_COMPLETION)
    input_rate = model.input_price_per_1m_tokens or Decimal("0.15")
    output_rate = model.output_price_per_1m_tokens or Decimal("0.60")

    total_tokens = _RESERVATION_MAX_TOOL_CALLS * _AVG_TOKENS_PER_ITERATION
    # Half input, half output as a rough split
    usd_cost = (
        input_rate * (total_tokens // 2) / 1_000_000
        + output_rate * (total_tokens // 2) / 1_000_000
    )
    credits = (usd_cost / USD_PER_CREDIT).quantize(_CREDIT_QUANT)
    # Floor to at least 0.01 so we always reserve *something*
    return max(credits, Decimal("0.01"))


# Lua script for atomic check-and-reserve.
# KEYS[1] = credits_reserved:{shop_id}
# ARGV[1] = amount to reserve (integer hundredths)
# ARGV[2] = available balance (integer hundredths) — passed in from Python
#           after querying the DB ledger.
# Returns 1 on success, 0 if reservation would exceed available balance.
_RESERVE_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local to_reserve = tonumber(ARGV[1])
local available = tonumber(ARGV[2])
if (current + to_reserve) > available then
    return 0
end
redis.call('INCRBY', KEYS[1], to_reserve)
redis.call('EXPIRE', KEYS[1], 300)
return 1
"""


def reserve_ai_credits(*, shop_id: str, estimated_credits: Decimal | None = None) -> Decimal:
    """
    Atomically reserve credits for an upcoming AI turn.

    Returns the amount reserved on success.
    Raises ``InsufficientCreditsError`` if the reservation would take the
    tenant's effective balance (ledger minus already-reserved) negative.

    If Redis is unavailable, falls through without reserving — the DB-level
    ``deduct_ai_credits`` is still the authoritative safety net.
    """
    if estimated_credits is None:
        estimated_credits = estimate_turn_credits(shop_id=shop_id)

    estimated_credits = estimated_credits.quantize(_CREDIT_QUANT)
    if estimated_credits <= 0:
        return Decimal("0")

    # Integer hundredths for Redis (avoids floating point)
    amount_hundredths = int(estimated_credits * 100)
    balance = available_credits(shop_id=shop_id)
    balance_hundredths = int(balance * 100)

    try:
        from django_redis import get_redis_connection
        r = get_redis_connection("default")
        result = r.eval(_RESERVE_LUA, 1, _reservation_key(shop_id),
                        amount_hundredths, balance_hundredths)
        if result == 0:
            raise InsufficientCreditsError(
                f"Cannot reserve {estimated_credits} credits for shop {shop_id}: "
                f"available={balance}, already_reserved={_get_total_reserved(shop_id)}"
            )
    except InsufficientCreditsError:
        raise
    except Exception as exc:
        # Redis unavailable — log warning but don't block the turn.
        # The DB-level deduct_ai_credits still enforces the floor.
        logger.warning(
            "Credit reservation Redis error for shop=%s (proceeding without reservation): %s",
            shop_id, exc,
        )
        return Decimal("0")

    return estimated_credits


def reconcile_ai_credits(*, shop_id: str, reserved_credits: Decimal) -> None:
    """
    Release a credit reservation after the AI turn completes.

    Called after ``log_accumulated_usage`` has written the actual usage to the
    DB ledger. This simply decrements the Redis reservation counter by the
    originally reserved amount — the real deduction already happened via
    ``deduct_ai_credits``.
    """
    if reserved_credits <= 0:
        return
    amount_hundredths = int(reserved_credits.quantize(_CREDIT_QUANT) * 100)
    try:
        from django_redis import get_redis_connection
        r = get_redis_connection("default")
        r.decrby(_reservation_key(shop_id), amount_hundredths)
        # Floor to zero — don't let counter go negative from rounding
        raw = r.get(_reservation_key(shop_id))
        if raw is not None and int(raw) < 0:
            r.set(_reservation_key(shop_id), 0, ex=300)
    except Exception as exc:
        logger.warning(
            "Credit reconciliation Redis error for shop=%s: %s (counter will self-expire)",
            shop_id, exc,
        )


def release_ai_credits(*, shop_id: str, reserved_credits: Decimal) -> None:
    """
    Emergency release for crashed/failed AI turns.

    Identical to ``reconcile_ai_credits`` — separated as a distinct function
    for clarity in call sites (``finally`` blocks) and future audit logging.
    A crashed turn produced no output, so no DB deduction happened; we just
    need to free the reservation so the counter doesn't permanently lock
    credits away from the tenant.
    """
    reconcile_ai_credits(shop_id=shop_id, reserved_credits=reserved_credits)


class InsufficientCreditsError(Exception):
    """Raised when a credit reservation cannot be fulfilled."""
    pass
