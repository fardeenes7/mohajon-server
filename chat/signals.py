from django.dispatch import receiver
from catalog.signals import product_updated
from billing.signals import subscription_upgraded
from marketing.signals import page_connected

# Fields that are written by the embedding pipeline itself.
# A save that touches ONLY these fields must not re-trigger embedding —
# doing so creates an infinite loop: task saves status → signal → new task → …
_VECTOR_ONLY_FIELDS = frozenset({"embedding", "vector_status", "updated_at"})

@receiver(product_updated)
def handle_product_updated(sender, product_id, update_fields=None, **kwargs):
    # If update_fields is known and contains only vector bookkeeping fields,
    # this save came from the embedding pipeline itself — skip re-enqueuing.
    if update_fields is not None and update_fields.issubset(_VECTOR_ONLY_FIELDS):
        return
    from chat.tasks.rag import embed_product_specs
    embed_product_specs.delay(product_id=product_id)

@receiver(subscription_upgraded)
def handle_subscription_upgraded(sender, shop_id, **kwargs):
    from chat.tasks.rag import backfill_skipped_embeddings
    backfill_skipped_embeddings.delay(shop_id=shop_id)

@receiver(page_connected)
def handle_page_connected(sender, shop_id, page_id, connection_id=None, **kwargs):
    # A Facebook Page was connected/refreshed — subscribe it to Messenger
    # webhooks and configure its Messenger profile.
    from chat.tasks import onboard_messenger_page
    onboard_messenger_page.delay(shop_id=shop_id, page_id=page_id)
