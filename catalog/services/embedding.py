"""
Product embedding service — stores AI-generated vectors on Product rows.

Kept in a dedicated module to avoid importing pgvector/model dependencies
at package import time. Called from Celery tasks only.
"""
from __future__ import annotations

import logging

from core.models import VectorStatus  # noqa: F401 — re-exported for tasks

logger = logging.getLogger(__name__)


def update_product_embedding(
    *, product_id: str, vector: list[float] | None, status: str
) -> None:
    """Write an embedding vector + vector_status to the Product row.

    ``status`` should be a ``VectorStatus`` choice value (e.g. "CREATED").
    A missing product is logged and silently skipped so a stale task ID
    never crashes the worker.
    """
    from catalog.models import Product

    try:
        product = Product.objects.get(id=product_id, deleted_at__isnull=True)
        if vector is not None:
            product.embedding = vector
        product.vector_status = status
        product.save(update_fields=["embedding", "vector_status", "updated_at"])
    except Product.DoesNotExist:
        logger.warning("update_product_embedding: Product %s not found", product_id)
