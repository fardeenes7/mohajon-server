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
