from __future__ import annotations

import logging
from celery import shared_task
from ai.services import generate_description, generate_image

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    queue="ai_copy",
    name="ai.tasks.generate_product_copy",
)
def generate_product_copy(self, *, shop_id: str, prompt: str) -> dict[str, str]:
    """
    Generate product SEO titles/descriptions (EPIC A-02).
    """
    logger.info("generate_product_copy started for shop=%s", shop_id)
    
    try:
        content = generate_description(
            shop_id=shop_id,
            system_prompt="You are a professional SEO copywriter for e-commerce stores.",
            user_prompt=prompt,
        )
        return {"status": "success", "content": content}
    except Exception as exc:
        logger.error("generate_product_copy failed: %s", exc)
        self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    queue="ai_copy",
    name="ai.tasks.generate_ad_copy",
)
def generate_ad_copy(self, *, shop_id: str, prompt: str) -> dict[str, str]:
    """
    Generate Facebook/Instagram ad copy (EPIC A-02).
    """
    logger.info("generate_ad_copy started for shop=%s", shop_id)
    
    try:
        content = generate_description(
            shop_id=shop_id,
            system_prompt="You are a social media marketing expert specialized in high-conversion ads.",
            user_prompt=prompt,
        )
        return {"status": "success", "content": content}
    except Exception as exc:
        logger.error("generate_ad_copy failed: %s", exc)
        self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    queue="ai_image",
    name="ai.tasks.generate_ad_image",
)
def generate_ad_image(self, *, shop_id: str, prompt: str) -> dict[str, str]:
    """
    Generate ad banners or product graphics (EPIC A-02).
    """
    logger.info("generate_ad_image started for shop=%s", shop_id)
    
    try:
        image_url = generate_image(shop_id=shop_id, prompt=prompt)
        return {"status": "success", "image_url": image_url}
    except Exception as exc:
        logger.error("generate_ad_image failed: %s", exc)
        self.retry(exc=exc)


@shared_task(
    name="ai.tasks.sweep_expired_ai_credits",
    queue="default",
)
def sweep_expired_ai_credits() -> dict[str, int]:
    """
    Enforce AI credit expiry (global_business_rules_and_limits.md §3).

    Marks every lot past its ``expires_at`` as expired, zeroes its remaining
    credits, and reconciles each affected shop's cached balance. Idempotent:
    already-expired lots are skipped.
    """
    from django.db import transaction
    from django.utils import timezone

    from ai.models import AICreditLot
    from ai.services.ai_credits import _sync_cached_balance

    now = timezone.now()
    expired_qs = AICreditLot.objects.filter(
        deleted_at__isnull=True,
        is_expired=False,
        expires_at__isnull=False,
        expires_at__lte=now,
    )

    affected_shops: set[str] = set()
    lots_expired = 0

    with transaction.atomic():
        for lot in expired_qs.select_for_update():
            lot.is_expired = True
            lot.credits_remaining = 0
            lot.is_exhausted = True
            lot.save(
                update_fields=[
                    "is_expired",
                    "credits_remaining",
                    "is_exhausted",
                    "updated_at",
                ]
            )
            affected_shops.add(str(lot.shop_id))
            lots_expired += 1

    for shop_id in affected_shops:
        _sync_cached_balance(shop_id)

    logger.info(
        "sweep_expired_ai_credits: expired %d lots across %d shops",
        lots_expired,
        len(affected_shops),
    )
    return {"lots_expired": lots_expired, "shops_affected": len(affected_shops)}
