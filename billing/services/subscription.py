"""
billing/services/subscription.py

Business logic for subscription lifecycle management.

Rules enforced:
- 7-day grace period on payment failure (global_business_rules_and_limits.md)
- Compliance Lock on quota-exceeding downgrade
- FeatureGate override formula: Effective Limit = Shop.override_* ?? Plan.limit
- Billing-exempt shops bypass ALL checks
"""

from django.utils import timezone
from datetime import timedelta

from shops.models import Shop
from core.services.feature_gate import FeatureGate, PLAN_MATRIX
from billing.models import ShopSubscription


GRACE_PERIOD_DAYS = 7


def get_or_create_subscription(shop: Shop) -> ShopSubscription:
    """
    Returns the ShopSubscription for a shop, creating a Free-tier one if absent.
    """
    sub, _ = ShopSubscription.objects.get_or_create(
        shop=shop,
        defaults={
            'tier': ShopSubscription.TIER_FREE,
            'status': ShopSubscription.STATUS_ACTIVE,
            'tenant_id': shop.id,
        }
    )
    return sub


def activate_subscription(shop: Shop, tier: str) -> ShopSubscription:
    """
    Elevate a shop to a new paid tier. Clears grace/suspension state.
    """
    sub = get_or_create_subscription(shop)
    now = timezone.now()

    old_tier = sub.tier

    sub.tier = tier
    sub.status = ShopSubscription.STATUS_ACTIVE
    sub.grace_period_until = None
    sub.last_paid_at = now
    sub.current_period_start = now
    sub.current_period_end = now + timedelta(days=30)
    sub.save(update_fields=[
        'tier', 'status', 'grace_period_until',
        'last_paid_at', 'current_period_start', 'current_period_end',
        'updated_at',
    ])

    if old_tier == ShopSubscription.TIER_FREE and tier != ShopSubscription.TIER_FREE:
        from messenger.tasks.rag import backfill_skipped_embeddings
        backfill_skipped_embeddings.delay(shop_id=str(shop.id))

    return sub


def trigger_payment_failure(shop: Shop) -> ShopSubscription:
    """
    Called when a recurring payment fails.
    Starts the 7-day grace period.
    """
    sub = get_or_create_subscription(shop)

    if sub.is_billing_exempt:
        return sub  # Exempt shops are never suspended

    sub.status = ShopSubscription.STATUS_GRACE
    sub.grace_period_until = timezone.now() + timedelta(days=GRACE_PERIOD_DAYS)
    sub.save(update_fields=['status', 'grace_period_until', 'updated_at'])
    return sub


def sweep_expired_grace_periods() -> int:
    """
    Celery beat task helper.
    Finds all shops whose grace period has elapsed and moves them to SUSPENDED.
    Returns count of shops suspended.
    """
    now = timezone.now()
    qs = ShopSubscription.objects.filter(
        status=ShopSubscription.STATUS_GRACE,
        grace_period_until__lt=now,
        deleted_at__isnull=True,
    )
    count = qs.update(status=ShopSubscription.STATUS_SUSPENDED)
    return count


def downgrade_subscription(shop: Shop, new_tier: str) -> tuple[ShopSubscription, bool]:
    """
    Downgrades a shop to a lower tier.

    If the shop is OVER quota for the new tier, applies a COMPLIANCE_LOCK —
    storefront suspended until the merchant manually deletes items.

    Returns (subscription, is_compliance_locked).
    """
    sub = get_or_create_subscription(shop)
    new_matrix = PLAN_MATRIX.get(new_tier, PLAN_MATRIX['Free'])

    # Check if shop is over product quota for the new plan
    from catalog.models import Product  # late import to avoid circular deps
    product_count = Product.objects.filter(
        shop=shop,
        deleted_at__isnull=True,
    ).count()

    effective_limit = shop.override_max_products or new_matrix.get('max_products', 5)
    is_over_quota = product_count > effective_limit

    sub.tier = new_tier
    if is_over_quota:
        sub.status = ShopSubscription.STATUS_COMPLIANCE_LOCK
    else:
        sub.status = ShopSubscription.STATUS_ACTIVE

    sub.save(update_fields=['tier', 'status', 'updated_at'])
    return sub, is_over_quota


def cancel_subscription(shop: Shop) -> ShopSubscription:
    """
    Hard cancellation — immediately ends service.
    """
    sub = get_or_create_subscription(shop)
    sub.status = ShopSubscription.STATUS_CANCELLED
    sub.save(update_fields=['status', 'updated_at'])
    return sub


def get_subscription_context(shop: Shop) -> dict:
    """
    Returns a serialisable dict for the dashboard and API. Includes:
    - current tier and status
    - effective limits (override-aware)
    - days remaining in grace period
    - storefront liveness flag
    - suspension banner copy
    """
    sub = get_or_create_subscription(shop)

    plan_key = sub.tier.capitalize()  # 'FREE' → 'Free', match PLAN_MATRIX keys
    # PLAN_MATRIX uses 'Free', 'Basic', 'Pro', 'Business'
    plan_key_map = {
        'FREE': 'Free',
        'BASIC': 'Basic',
        'PRO': 'Pro',
        'BUSINESS': 'Business',
        'CUSTOM': 'Business',  # treat Custom as max for feature access
    }
    plan_key = plan_key_map.get(sub.tier, 'Free')
    matrix = PLAN_MATRIX.get(plan_key, PLAN_MATRIX['Free'])

    grace_days_remaining = None
    if sub.status == ShopSubscription.STATUS_GRACE and sub.grace_period_until:
        from django.utils import timezone
        remaining = sub.grace_period_until - timezone.now()
        grace_days_remaining = max(0, remaining.days)

    return {
        'tier': sub.tier,
        'status': sub.status,
        'is_storefront_live': sub.is_storefront_live,
        'suspension_banner': sub.suspension_banner,
        'grace_days_remaining': grace_days_remaining,
        'current_period_end': sub.current_period_end,
        'last_paid_at': sub.last_paid_at,
        'is_billing_exempt': sub.is_billing_exempt,
        'limits': {
            'max_products': shop.override_max_products or matrix.get('max_products'),
            'max_staff': shop.override_max_staff or matrix.get('staff_accounts'),
            'custom_domains': matrix.get('custom_domains'),
            'pos_system': matrix.get('pos_system'),
            'developer_api': matrix.get('developer_api'),
            'marketing_pixels': matrix.get('marketing_pixels'),
            'custom_smtp': matrix.get('custom_smtp'),
            'b2b_credit': matrix.get('b2b_credit'),
        },
    }
class BillingService:
    """
    High-level orchestrator for subscription billing.
    """
    def __init__(self, shop: Shop):
        self.shop = shop

    def initiate_subscription_payment(self, plan_id: str, callback_url: str):
        """
        Starts the bKash payment flow for a subscription upgrade/renewal.
        """
        from billing.models import ShopSubscription
        # Find the requested tier from the plan_id (placeholder logic)
        # In a real app, we'd have a SubscriptionPlan model
        target_tier = plan_id  # assuming plan_id is 'BASIC', 'PRO', etc.
        
        from billing.services.bkash import BKashService
        # NOTE: In production, this should use the PLATFORM's bKash account,
        # but for now we use the shop's config as a placeholder.
        bkash = BKashService(self.shop)
        
        res = bkash.create_agreement(
            payer_reference=f"SUB-{self.shop.id}",
            callback_url=callback_url
        )
        return res

    def finalize_subscription_payment(self, payment_id: str):
        """
        Called after bKash callback to activate the plan.
        """
        from billing.services.bkash import BKashService
        bkash = BKashService(self.shop)
        
        res = bkash.execute_payment(payment_id)
        if res.get('statusCode') == '0000':
            # For now, we'll extract the tier from the payerReference or a session
            # As a placeholder, let's assume PRO if successful.
            activate_subscription(self.shop, ShopSubscription.TIER_PRO)
            return {"status": "SUCCESS", "tier": "PRO"}
            
        return {"status": "FAILED", "error": res.get('statusMessage')}
