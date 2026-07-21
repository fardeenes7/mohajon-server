"""
Comment Auto-Reply & DM Funnel (EPIC E).

When a Facebook post comment arrives and auto-reply is toggled on for
that post, this service:
  1. Checks the product's stock — freezes if zero.
  2. Deduplicates on (psid + post_id) key via WebhookLog.
  3. Posts a public comment reply on the FB post.
  4. Sends a Messenger DM with the product card(s).

Dedup key: psid + post_id   (NOT psid alone — per global_business_rules)
"""
from __future__ import annotations

import logging

from webhooks.services import webhook_event_already_processed, webhook_log_event
from webhooks.models import WebhookProvider, WebhookProcessingStatus

logger = logging.getLogger(__name__)

from chat.services.send_api import reply_to_comment, send_generic_template

_COMMENT_REPLY_TEXT = "Price sent to your inbox! 💌"
_STOCK_ZERO_REPLY = "This item is currently out of stock. Stay tuned! 🙏"


def handle_comment_auto_reply(
    *,
    shop_id: str,
    page_id: str,
    psid: str,
    post_id: str,
    comment_id: str,
    page_access_token: str,
    product_ids: list[str],
) -> bool:
    """
    Process a comment auto-reply event.

    Returns True if the DM was sent, False if skipped (duplicate / stock-zero).
    """
    dedup_key = f"COMMENT_AUTOREPLY:{psid}:{post_id}"

    # A comment and a DM from the same user share a PSID on the same page, so
    # resolve the display name through the common (page, psid)-keyed seam here too.
    # Deduplicated in the profile service, so this is a cheap no-op after the first.
    from chat.services.profile import queue_display_name_sync
    queue_display_name_sync(shop_id=str(shop_id), page_id=str(page_id), psid=str(psid))

    if webhook_event_already_processed(provider=WebhookProvider.META, external_event_id=dedup_key):
        logger.info("Comment auto-reply deduplicated: %s", dedup_key)
        return False

    # Check if all linked products are out of stock
    from catalog.models import Product
    products = list(
        Product.objects.filter(id__in=product_ids, shop_id=shop_id, deleted_at__isnull=True)
    )
    all_out_of_stock = all(p.total_stock <= 0 for p in products) if products else True



    if all_out_of_stock:
        # Post a public out-of-stock comment and freeze — do NOT send DM
        try:
            reply_to_comment(
                comment_id=comment_id,
                message=_STOCK_ZERO_REPLY,
                token=page_access_token,
            )
        except Exception as exc:
            logger.warning("Failed to post stock-zero comment: %s", exc)
        webhook_log_event(
            provider=WebhookProvider.META,
            external_event_id=dedup_key,
            event_type="COMMENT_AUTO_REPLY_STOCK_ZERO",
            shop_id=shop_id,
            status=WebhookProcessingStatus.PROCESSED,
        )
        return False

    # Post public comment reply
    try:
        reply_to_comment(
            comment_id=comment_id,
            message=_COMMENT_REPLY_TEXT,
            token=page_access_token,
        )
    except Exception as exc:
        logger.warning("Failed to post public comment reply: %s", exc)

    # Build product cards for the DM carousel (max 10)
    elements = []
    for product in products[:10]:
        from shops.selectors import get_shop
        try:
            shop = get_shop(shop_id)
            if shop:
                storefront_url = f"https://{shop.subdomain}.mohajon.store/products/{product.slug}"
            else:
                storefront_url = "#"
        except Exception:
            storefront_url = "#"

        elements.append({
            "title": product.name,
            "subtitle": f"৳{product.base_price}",
            "image_url": None,  # resolved from product media in a real scenario
            "buttons": [
                {"type": "postback", "title": "🛒 Order Now", "payload": f"ORDER_NOW:{product.id}"},
                {"type": "web_url", "title": "🔗 View in Shop", "url": storefront_url},
            ],
        })

    try:
        send_generic_template(psid=psid, elements=elements, token=page_access_token)
    except Exception as exc:
        logger.error("Failed to send DM after comment auto-reply: %s", exc)
        webhook_log_event(
            provider=WebhookProvider.META,
            external_event_id=dedup_key,
            event_type="COMMENT_AUTO_REPLY",
            shop_id=shop_id,
            status=WebhookProcessingStatus.FAILED,
            error_message=str(exc),
        )
        return False

    webhook_log_event(
        provider=WebhookProvider.META,
        external_event_id=dedup_key,
        event_type="COMMENT_AUTO_REPLY",
        shop_id=shop_id,
        status=WebhookProcessingStatus.PROCESSED,
    )
    return True
