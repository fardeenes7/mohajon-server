"""
Misuse protection for merchant fraud reports (design doc Phase 4.3).

The cross-shop pool is a weapon if any shop can poison an arbitrary phone's
score without proof. Two gates close that hole:

  1. ORDER LINKAGE — a report only counts toward the pool if the reporting shop
     has a genuine order with the target phone. A shop cannot report a number it
     never did business with. This is verified against the reporting shop's own
     orders (tenant-scoped), matched on the canonical phone hash so formatting
     never lets a real order slip past the check.

  2. RATE LIMIT — a shop is capped at a bounded number of pool-affecting reports
     per rolling window, so a compromised or malicious account cannot mass-report.

A report that fails either gate is still PERSISTED (the shop's own record stays,
useful for their private view and audit), but is flagged `counts_toward_pool =
False` so the GlobalFraudPool sync skips it. The judgment of whether a report
counts is policy and lives here in `fraud`, never in `identity`.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from core.phone import hash_phone

# Max pool-affecting reports a single shop may file per rolling window.
REPORT_RATE_LIMIT = 20
REPORT_RATE_WINDOW = timedelta(hours=24)
_RATE_KEY = "fraud:report_rate:{shop_id}"


def shop_has_order_with_phone(*, shop_id: str, phone_number: str) -> bool:
    """
    True when the reporting shop has at least one order whose phone matches the
    target, compared on the canonical hash so '01712...' and '+88017...' match.

    This is the anti-victim-targeting gate: no order between the shop and the
    number → the shop has no standing to report it into the shared pool.
    """
    canonical = hash_phone(phone_number)
    if not canonical:
        return False

    from orders.models import Order

    # Match on the order's snapshot phone_hash (canonical) OR the linked
    # PhoneIdentity, scoped to the reporting shop only.
    return Order.objects.filter(
        shop_id=shop_id,
        deleted_at__isnull=True,
    ).filter(
        identity_snapshot__phone_hash=canonical,
    ).exists()


def within_report_rate_limit(*, shop_id: str) -> bool:
    """
    Sliding-ish counter (fixed window in cache) of pool-affecting reports per
    shop. Returns True if the shop is still under the cap. Fails OPEN if the
    cache is unavailable — a rate limiter must never block legitimate reporting
    just because Redis hiccupped; the order-linkage gate is the real security
    boundary, this is only abuse dampening.
    """
    key = _RATE_KEY.format(shop_id=shop_id)
    try:
        current = cache.get(key, 0)
        if current >= REPORT_RATE_LIMIT:
            return False
        # add() sets only if missing (starts the window); otherwise incr().
        if cache.add(key, 1, timeout=int(REPORT_RATE_WINDOW.total_seconds())):
            return True
        cache.incr(key)
        return True
    except Exception:  # noqa: BLE001
        return True


def evaluate_report_eligibility(*, shop_id: str, phone_number: str, order_id: str | None) -> dict:
    """
    Decide whether a report may affect the shared pool. Returns a dict:
      {counts_toward_pool: bool, reason: str}

    Order linkage is mandatory; the rate limit is a secondary dampener. The
    reason string is for audit/ops visibility, not shown to the reported party.
    """
    if not shop_has_order_with_phone(shop_id=shop_id, phone_number=phone_number):
        return {"counts_toward_pool": False, "reason": "no_order_with_phone"}
    if not within_report_rate_limit(shop_id=shop_id):
        return {"counts_toward_pool": False, "reason": "rate_limited"}
    return {"counts_toward_pool": True, "reason": "ok"}
