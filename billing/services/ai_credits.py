from __future__ import annotations

import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from billing.models import AICreditPackage, AICreditTopUp, PaymentTransaction
from billing.services.bkash import BKashService
from shops.models import Shop, ShopSettings

logger = logging.getLogger(__name__)


class AICreditService:
    def __init__(self, shop: Shop):
        self.shop = shop

    def initiate_topup(self, package_id: str, callback_url: str) -> dict:
        """
        Starts the bKash payment flow for an AI credit top-up.
        """
        try:
            package = AICreditPackage.objects.get(id=package_id, is_active=True)
        except AICreditPackage.DoesNotExist:
            raise ValueError("Invalid or inactive credit package.")

        # 1. Create a pending TopUp record
        topup = AICreditTopUp.objects.create(
            shop=self.shop,
            tenant_id=self.shop.id,
            package=package,
            credits_purchased=package.credits,
            amount_paid_bdt=package.retail_price_bdt,
            status="PENDING"
        )

        # 2. Initiate bKash Agreement (Placeholder: should use Platform account)
        bkash = BKashService(self.shop)
        res = bkash.create_agreement(
            payer_reference=f"TOPUP-{topup.id}",
            callback_url=callback_url
        )
        
        # In a real bKash flow, we'd store the paymentID in the topup record
        # but bKash's create_agreement returns a bkashURL.
        return res

    def finalize_topup(self, payment_id: str) -> dict:
        """
        Executes the bKash payment and updates the shop's credit balance.
        """
        bkash = BKashService(self.shop)
        res = bkash.execute_payment(payment_id)
        
        if res.get('statusCode') == '0000':
            # 1. Find the TopUp record from the payerReference
            # payerReference was "TOPUP-{topup.id}"
            payer_ref = res.get('payerReference', '')
            if not payer_ref.startswith("TOPUP-"):
                raise ValueError("Invalid payer reference in bKash response.")
            
            topup_id = payer_ref.replace("TOPUP-", "")
            try:
                topup = AICreditTopUp.objects.get(id=topup_id, shop=self.shop)
            except AICreditTopUp.DoesNotExist:
                raise ValueError("Top-up record not found.")

            # 2. Update TopUp record and link transaction
            with transaction.atomic():
                # Find the transaction created by bkash.execute_payment
                trx = PaymentTransaction.objects.filter(
                    shop=self.shop, 
                    external_transaction_id=res.get('trxID')
                ).first()
                
                topup.status = "COMPLETED"
                topup.transaction = trx
                # 60-day expiry per business rules §3
                topup.expires_at = timezone.now() + timedelta(days=60)
                topup.save()

                # 3. Grant the purchased credits as a ledger lot. The ledger
                #    is the source of truth; grant_ai_credits also reconciles
                #    the cached ShopSettings.ai_credit_balance.
                from ai.models import AICreditCategory
                from ai.services.ai_credits import (
                    available_credits,
                    grant_ai_credits,
                )

                # Ensure a settings row exists so the cached balance can sync.
                ShopSettings.objects.get_or_create(
                    shop=self.shop,
                    defaults={'tenant_id': self.shop.id},
                )

                grant_ai_credits(
                    shop_id=self.shop.id,
                    credits=Decimal(str(topup.credits_purchased)),
                    category=AICreditCategory.PURCHASED,
                    expires_at=topup.expires_at,
                    note=f"Top-up {topup.id}",
                    source_topup=topup,
                )

            return {
                "status": "SUCCESS",
                "credits_added": topup.credits_purchased,
                "new_balance": float(available_credits(shop_id=self.shop.id)),
            }
            
        return {"status": "FAILED", "error": res.get('statusMessage')}
