from __future__ import annotations

import logging
import time
from celery import shared_task
from django_redis import get_redis_connection

from ai.services import AIGateway

logger = logging.getLogger(__name__)

# ── Embedding rate-limit circuit-breaker ─────────────────────────────────────
# When any embedding task hits a 429, it writes this Redis key with a TTL equal
# to its backoff delay.  Every other task checks this key before touching the
# AI gateway — if the cooldown is still active they re-schedule themselves for
# the remaining seconds instead of firing another request.  This stops 50+
# queued tasks from hammering the gateway and perpetuating the rate limit.
_RATE_LIMIT_KEY = "embedding:rate_limited_until"


def _set_global_cooldown(seconds: int) -> None:
    """Signal all embedding workers to back off for `seconds`."""
    r = get_redis_connection("default")
    until = int(time.time()) + seconds
    # Only extend the cooldown — never shorten an existing one.
    existing = r.get(_RATE_LIMIT_KEY)
    if existing is None or int(existing) < until:
        r.set(_RATE_LIMIT_KEY, until, ex=seconds + 5)  # +5s safety margin
    logger.warning(
        "Embedding circuit-breaker SET: all embedding tasks will back off for %ds (until=%d).",
        seconds, until,
    )


def _get_cooldown_remaining() -> int:
    """
    Returns the number of seconds remaining in the global cooldown, or 0
    if no cooldown is active.
    """
    r = get_redis_connection("default")
    val = r.get(_RATE_LIMIT_KEY)
    if val is None:
        return 0
    remaining = int(val) - int(time.time())
    return max(remaining, 0)


def _is_rate_limit_error(exc: Exception) -> bool:
    """Helper to detect 429 rate limit errors from OpenAI SDK / Gateway."""
    from openai import RateLimitError
    if isinstance(exc, RateLimitError):
        return True
    exc_str = str(exc)
    return "429" in exc_str or "rate_limit_exceeded" in exc_str or "rate-limited" in exc_str


@shared_task(
    bind=True,
    max_retries=5,
    default_retry_delay=10,
    queue="ai_rag",
    name="chat.tasks.embed_faq_entry",
)
def embed_faq_entry(self, *, faq_entry_id: str) -> None:
    """
    Generate pgvector embedding for a FAQEntry and persist to DB.
    """
    from chat.models import FAQEntry

    try:
        entry = FAQEntry.objects.select_related("shop__subscription").get(id=faq_entry_id, deleted_at__isnull=True)
    except FAQEntry.DoesNotExist:
        logger.warning("embed_faq_entry: FAQEntry %s not found", faq_entry_id)
        return

    from billing.models import ShopSubscription
    from core.models import VectorStatus

    if getattr(entry.shop, "subscription", None) and entry.shop.subscription.tier == ShopSubscription.TIER_FREE:
        logger.info("embed_faq_entry: Skipped FAQEntry %s (Shop on Free plan)", faq_entry_id)
        entry.vector_status = VectorStatus.SKIPPED
        entry.save(update_fields=["vector_status", "updated_at"])
        return

    # ── Circuit-breaker check ─────────────────────────────────────────────
    remaining = _get_cooldown_remaining()
    if remaining > 0:
        logger.info(
            "embed_faq_entry: circuit-breaker active (%ds remaining) — re-scheduling FAQEntry %s.",
            remaining, faq_entry_id,
        )
        raise self.retry(countdown=remaining, exc=None)

    text = f"Category: {entry.category}\nQuestion: {entry.question}\nAnswer: {entry.answer}"
    gateway = AIGateway(str(entry.shop_id))

    try:
        vector = gateway.call_embedding(text=text, dimensions=3072)
        entry.embedding = vector
        entry.vector_status = VectorStatus.CREATED
        entry.save(update_fields=["embedding", "vector_status", "updated_at"])
        logger.info("Embedded FAQEntry %s successfully.", faq_entry_id)
    except Exception as exc:
        if _is_rate_limit_error(exc):
            retry_delay = 60 * (2 ** self.request.retries)  # 60s, 120s, 240s…
            _set_global_cooldown(retry_delay)
            logger.warning(
                "Embedding rate-limited (429) for FAQEntry %s. Circuit-breaker set for %ds (retry %d/%d).",
                faq_entry_id, retry_delay, self.request.retries + 1, self.max_retries
            )
            entry.vector_status = VectorStatus.PENDING
            entry.save(update_fields=["vector_status", "updated_at"])
            self.retry(exc=exc, countdown=retry_delay)
        else:
            logger.error("Embedding failed for FAQEntry %s: %s", faq_entry_id, exc)
            entry.vector_status = VectorStatus.FAILED
            entry.save(update_fields=["vector_status", "updated_at"])
            self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=5,
    default_retry_delay=10,
    queue="ai_rag",
    name="chat.tasks.embed_product_specs",
)
def embed_product_specs(self, *, product_id: str) -> None:
    """
    Generate pgvector embedding for Product Specs and persist to DB.
    Includes: Name, Description, and Specifications JSON.
    """
    from catalog.models import Product

    try:
        product = Product.objects.select_related("shop__subscription").get(id=product_id, deleted_at__isnull=True)
    except Product.DoesNotExist:
        logger.warning("embed_product_specs: Product %s not found", product_id)
        return

    from billing.models import ShopSubscription
    from core.models import VectorStatus
    from catalog.services import update_product_embedding

    if getattr(product.shop, "subscription", None) and product.shop.subscription.tier == ShopSubscription.TIER_FREE:
        logger.info("embed_product_specs: Skipped Product %s (Shop on Free plan)", product_id)
        update_product_embedding(product_id=product_id, vector=None, status=VectorStatus.SKIPPED)
        return

    # ── Circuit-breaker check ─────────────────────────────────────────────
    remaining = _get_cooldown_remaining()
    if remaining > 0:
        logger.info(
            "embed_product_specs: circuit-breaker active (%ds remaining) — re-scheduling Product %s.",
            remaining, product_id,
        )
        raise self.retry(countdown=remaining, exc=None)

    # Build semantic text representation
    specs_str = "\n".join([f"{k}: {v}" for k, v in product.specifications.items()])
    text = (
        f"Product Name: {product.name}\n"
        f"Description: {product.description}\n"
        f"Specs:\n{specs_str}"
    )

    gateway = AIGateway(str(product.shop_id))

    try:
        vector = gateway.call_embedding(text=text, dimensions=3072)
        update_product_embedding(product_id=product_id, vector=vector, status=VectorStatus.CREATED)
        logger.info("Embedded Product %s successfully.", product_id)
    except Exception as exc:
        if _is_rate_limit_error(exc):
            retry_delay = 60 * (2 ** self.request.retries)  # 60s, 120s, 240s…
            _set_global_cooldown(retry_delay)
            logger.warning(
                "Embedding rate-limited (429) for Product %s. Circuit-breaker set for %ds (retry %d/%d).",
                product_id, retry_delay, self.request.retries + 1, self.max_retries
            )
            update_product_embedding(product_id=product_id, vector=None, status=VectorStatus.PENDING)
            self.retry(exc=exc, countdown=retry_delay)
        else:
            logger.error("Embedding failed for Product %s: %s", product_id, exc)
            update_product_embedding(product_id=product_id, vector=None, status=VectorStatus.FAILED)
            self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue="ai_rag",
    name="chat.tasks.backfill_skipped_embeddings",
)
def backfill_skipped_embeddings(self, *, shop_id: str) -> None:
    """
    Called when a shop upgrades from Free to Paid.
    Finds all FAQEntry and Product records with vector_status=SKIPPED
    and enqueues their individual embedding tasks.
    """
    from core.models import VectorStatus
    from chat.models import FAQEntry
    from catalog.models import Product

    faq_ids = FAQEntry.objects.filter(
        shop_id=shop_id,
        vector_status=VectorStatus.SKIPPED,
        deleted_at__isnull=True
    ).values_list('id', flat=True)

    for faq_id in faq_ids:
        embed_faq_entry.delay(faq_entry_id=str(faq_id))

    product_ids = Product.objects.filter(
        shop_id=shop_id,
        vector_status=VectorStatus.SKIPPED,
        deleted_at__isnull=True
    ).values_list('id', flat=True)

    for prod_id in product_ids:
        embed_product_specs.delay(product_id=str(prod_id))

    logger.info("Enqueued backfill for %d FAQs and %d Products for shop %s.", len(faq_ids), len(product_ids), shop_id)
