"""
Catalog signals.

1. Auto-create ShopTrackingConfig when a new Shop is created.
2. Update Product.search_vector when name/description changes (Postgres FTS fallback).
3. Enqueue CatalogIndexingTask to sync Product to Meilisearch on create/update/delete.
"""
from django.contrib.postgres.search import SearchVector
from django.db import models, transaction
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender="shops.Shop")
def create_shop_tracking_config(sender, instance, created, **kwargs):
    """Auto-create a ShopTrackingConfig record when a new Shop is saved."""
    if created:
        from catalog.models.tracking import ShopTrackingConfig
        ShopTrackingConfig.objects.get_or_create(shop=instance)


@receiver(post_save, sender="catalog.Product")
def update_product_search_vector(sender, instance, **kwargs):
    """
    Rebuild the product's Postgres full-text search vector after each save.
    Uses update() to avoid recursive signal emission.

    NOTE: This is kept as a legacy signal for dashboard keyword search fallback
    (product_list_for_dashboard still queries Postgres directly for status/category
    filters).
    """
    from catalog.models.product import Product

    Product.objects.filter(pk=instance.pk).update(
        search_vector=(
            SearchVector("name", weight="A")
            + SearchVector("description", weight="B")
            + SearchVector("sku", weight="C")
        )
    )



@receiver(post_save, sender="catalog.Product")
def trigger_product_rag_indexing(sender, instance, **kwargs):
    """
    EPIC A-03: Enqueue an async task to generate semantic embeddings for RAG.
    """
    from chat.tasks.rag import embed_product_specs

    product_id = str(instance.pk)

    def _enqueue():
        embed_product_specs.delay(product_id=product_id)

    transaction.on_commit(_enqueue)


@receiver(post_save, sender="catalog.ProductVariant")
def sync_stock_to_redis(sender, instance, **kwargs):
    """
    Syncs the database stock_quantity to Redis for atomic reservations.
    """
    from django_redis import get_redis_connection
    
    product_id = str(instance.product_id)
    variant_id = str(instance.pk)
    key = f"stock:{product_id}:{variant_id}"
    
    r = get_redis_connection("default")
    r.set(key, instance.stock_quantity)


@receiver(post_save, sender="catalog.Product")
def sync_product_stock_to_redis(sender, instance, **kwargs):
    """
    Syncs the product-level total_stock to Redis for atomic reservations
    if it's a simple product (no variants).
    """
    if not instance.variants.exists():
        from django_redis import get_redis_connection
        
        product_id = str(instance.pk)
        key = f"stock:{product_id}"
        
        r = get_redis_connection("default")
        r.set(key, instance.total_stock)
