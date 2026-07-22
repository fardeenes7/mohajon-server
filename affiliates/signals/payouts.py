from django.db.models.signals import post_save
from django.dispatch import receiver
from billing.models import PaymentTransaction
from affiliates.models import Referral
from django.db import transaction
from decimal import Decimal

@receiver(post_save, sender=PaymentTransaction)
def handle_referral_payout(sender, instance, created, **kwargs):
    """
    When a payment is completed, check if the shop was referred.
    If so, verify the referral and apply rewards.
    """
    # Only process completed transactions for subscription or topups
    if instance.status == PaymentTransaction.STATUS_COMPLETED:
        # Check if this shop was referred
        try:
            referral = Referral.objects.select_related('referrer_shop', 'referred_shop').get(
                referred_shop=instance.shop,
                status='PENDING'
            )
            
            with transaction.atomic():
                # 1. Verify referral
                referral.status = 'VERIFIED'

                # Reward logic: 500 BDT in AI credits as a thank you
                reward = Decimal('500.00')
                referral.reward_amount = reward

                # 2. Grant credits to the referrer as a ledger lot. The ledger
                #    is the source of truth; grant_ai_credits keeps the cached
                #    ShopSettings.ai_credit_balance in sync.
                from shops.models import ShopSettings
                from ai.models import AICreditCategory
                from ai.services.ai_credits import grant_ai_credits

                # Ensure a settings row exists so the cached balance can sync.
                ShopSettings.objects.get_or_create(shop=referral.referrer_shop)

                grant_ai_credits(
                    shop_id=referral.referrer_shop_id,
                    credits=reward,
                    category=AICreditCategory.REFERRAL,
                    note=f"Referral reward ({referral.id})",
                )

                referral.reward_applied = True
                referral.save()
                
        except Referral.DoesNotExist:
            pass
