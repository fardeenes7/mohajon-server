from __future__ import annotations
import logging
from core.models import VectorStatus

logger = logging.getLogger(__name__)

def update_product_embedding(*, product_id: str, vector: list[float] | None, status: str) -> None:
    from catalog.models import Product
    try:
        product = Product.objects.get(id=product_id, deleted_at__isnull=True)
        if vector is not None:
            product.embedding = vector
        product.vector_status = status
        product.save(update_fields=["embedding", "vector_status", "updated_at"])
    except Product.DoesNotExist:
        logger.warning("update_product_embedding: Product %s not found", product_id)
