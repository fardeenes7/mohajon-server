from django.db.models.signals import post_save
from django.dispatch import receiver
from fraud.models import FraudReport, GlobalFraudPool, FraudConfig
from django.db import transaction
from users.models import Address, PhoneIdentity

from fraud.services.identity_linking import link_identity_on_address_create, link_identity_on_phone_claim

@receiver(post_save, sender=FraudReport)
def sync_fraud_to_global_pool(sender, instance, created, **kwargs):
    """
    Update the global (non-tenant) fraud pool when a merchant reports fraud.
    Only syncs if the shop has opted-in to pooling.
    """
    if created:
        # Misuse protection (design doc Phase 4.3): a report only contributes to
        # the cross-shop pool when it is backed by a genuine order between the
        # reporting shop and the target (counts_toward_pool, set by
        # fraud.services.misuse at creation). This is what stops a shop from
        # poisoning an arbitrary phone's pool score to target a victim.
        if not instance.counts_toward_pool:
            return

        config, _ = FraudConfig.objects.get_or_create(shop=instance.shop)
        if not config.opt_in_pooling:
            return

        with transaction.atomic():
            pool, _ = GlobalFraudPool.objects.get_or_create(phone_hash=instance.phone_hash)
            
            if instance.reason == 'RTO':
                pool.rto_count += 1
            elif instance.reason == 'FAKE_ORDER':
                pool.fake_order_count += 1
            elif instance.reason == 'HARASSMENT':
                pool.harassment_count += 1
            elif instance.reason == 'UNPAID':
                pool.unpaid_count += 1
                
            pool.save()


@receiver(post_save, sender=Address)
def link_identity_on_address(sender, instance, created, **kwargs):
    if created:
        link_identity_on_address_create(instance)


@receiver(post_save, sender=PhoneIdentity)
def link_identity_on_phone(sender, instance, created, **kwargs):
    if instance.is_verified and instance.user_id:
        link_identity_on_phone_claim(instance, instance.user)
