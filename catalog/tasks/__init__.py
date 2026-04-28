"""
Catalog Celery Tasks.

auto_publish_scheduled: Sweeps for SCHEDULED products whose publish_at
has passed and flips them to PUBLISHED. Runs every 5 minutes via Beat.

catalog_index_product: Syncs a single Product record to Meilisearch.
Called by the post_save signal — async so it never blocks the API response.
Fix 6.8 (post_v03_debrief.md).

catalog_reindex_all: One-shot task to rebuild the entire Meilisearch products
index from Postgres. Run manually after initial deploy:
  celery -A mohajon call catalog.tasks.catalog_reindex_all
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(queue="default", name="catalog.tasks.auto_publish_scheduled")
def auto_publish_scheduled():
    """
    Finds all SCHEDULED products whose publish_at has passed and publishes them.
    """
    from django.utils import timezone
    from catalog.models import Product, ProductStatus

    now = timezone.now()
    qs = Product.objects.filter(
        status=ProductStatus.SCHEDULED,
        publish_at__lte=now,
        deleted_at__isnull=True,
    )
    count = qs.count()
    qs.update(status=ProductStatus.PUBLISHED)

    if count:
        logger.info("auto_publish_scheduled: Published %d products.", count)

    return {"published": count}


@shared_task(queue="default", name="catalog.tasks.catalog_index_product", max_retries=3)
def catalog_index_product(product_id: str):
    """
    Fix 6.8: Syncs a single Product to Meilisearch.

    Called via post_save signal (transaction.on_commit so the row is
    committed before we read it). Soft-deleted products are removed from
    the index instead of being re-indexed.
    """
    from catalog.models import Product
    from catalog.services.search import index_product, delete_product_from_index

    try:
        product = (
            Product.objects.select_related("category")
            .prefetch_related("product_media__media", "variants")
            .get(id=product_id)
        )
        if product.deleted_at is not None:
            delete_product_from_index(str(product_id))
            logger.debug("catalog_index_product: removed soft-deleted product %s", product_id)
        else:
            index_product(product)
            logger.debug("catalog_index_product: indexed product %s", product_id)
    except Product.DoesNotExist:
        # Product may have been hard-deleted (admin action); remove from index
        delete_product_from_index(str(product_id))
        logger.warning("catalog_index_product: product %s not found — removed from index", product_id)

    return {"product_id": product_id}


@shared_task(queue="default", name="catalog.tasks.catalog_reindex_all")
def catalog_reindex_all():
    """
    Full reindex: reads all non-deleted Products from Postgres and upserts them
    into Meilisearch in batches of 100.

    Run once after initial deploy or after index corruption:
      celery -A mohajon call catalog.tasks.catalog_reindex_all
    """
    from catalog.models import Product
    from catalog.services.search import get_or_create_index, build_product_document

    qs = (
        Product.objects.filter(deleted_at__isnull=True)
        .select_related("category")
        .prefetch_related("product_media__media")
        .order_by("created_at")
    )

    index = get_or_create_index()
    batch = []
    total = 0
    BATCH_SIZE = 100

    for product in qs.iterator(chunk_size=BATCH_SIZE):
        batch.append(build_product_document(product))
        if len(batch) >= BATCH_SIZE:
            index.add_documents(batch)
            total += len(batch)
            batch = []

    if batch:
        index.add_documents(batch)
        total += len(batch)

    logger.info("catalog_reindex_all: indexed %d products.", total)
    return {"indexed": total}

