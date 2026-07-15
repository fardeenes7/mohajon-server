"""
Catalog Celery Tasks.

auto_publish_scheduled: Sweeps for SCHEDULED products whose publish_at
has passed and flips them to PUBLISHED. Runs every 5 minutes via Beat.

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


