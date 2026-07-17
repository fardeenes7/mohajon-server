from django.dispatch import receiver
from catalog.signals import product_updated
from billing.signals import subscription_upgraded

@receiver(product_updated)
def handle_product_updated(sender, product_id, **kwargs):
    from chat.tasks.rag import embed_product_specs
    embed_product_specs.delay(product_id=product_id)

@receiver(subscription_upgraded)
def handle_subscription_upgraded(sender, shop_id, **kwargs):
    from chat.tasks.rag import backfill_skipped_embeddings
    backfill_skipped_embeddings.delay(shop_id=shop_id)
