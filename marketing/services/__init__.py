import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from marketing.models import (
    SocialConnection,
    SocialConnectionStatus,
    ProductSocialPostLog,
    SocialPostStatus,
)


def normalize_meta_page_payload(page: dict) -> dict:
    return {
        "id": str(page.get("id", "")),
        "name": str(page.get("name", "")),
        "access_token": str(page.get("access_token", "")),
    }


def upsert_social_connection(
    *,
    shop_id: str,
    provider: str,
    page_id: str,
    page_name: str,
    access_token: str,
    expires_in: int | None,
):
    # Check if page is already actively connected to a different shop
    existing_other = SocialConnection.objects.filter(
        page_id=str(page_id),
        status=SocialConnectionStatus.ACTIVE,
        deleted_at__isnull=True,
    ).exclude(shop_id=shop_id).first()

    if existing_other:
        raise ValueError(
            f"Facebook Page '{page_name}' is already connected to another Mohajon store. Please disconnect it from that store before connecting here."
        )

    expires_at = timezone.now() + timedelta(seconds=expires_in) if expires_in else None

    connection, _ = SocialConnection.objects.update_or_create(
        shop_id=shop_id,
        provider=provider,
        page_id=page_id,
        deleted_at__isnull=True,
        defaults={
            "tenant_id": shop_id,
            "page_name": page_name,
            "access_token": access_token,
            "token_expires_at": expires_at,
            "last_refreshed_at": timezone.now(),
            "status": SocialConnectionStatus.ACTIVE,
            "last_error": "",
        },
    )

    # Notify receivers (chat) so the page gets subscribed to Messenger webhooks
    # and its Messenger profile configured. Fire after commit so the connection
    # row (and its token) is durable before any Graph call reads it.
    from marketing.signals import page_connected

    def _emit():
        page_connected.send(
            sender=SocialConnection,
            shop_id=str(shop_id),
            page_id=str(page_id),
            connection_id=str(connection.id),
        )

    transaction.on_commit(_emit)
    return connection


def disconnect_social_connection(*, shop_id: str, connection_id: str):
    connection = SocialConnection.objects.get(
        id=connection_id,
        shop_id=shop_id,
        deleted_at__isnull=True,
    )
    connection.status = SocialConnectionStatus.DISCONNECTED
    connection.access_token = ""
    connection.last_error = "Disconnected by merchant"
    connection.save(update_fields=["status", "access_token", "last_error", "updated_at"])
    return connection


def create_social_publish_log(*, shop_id: str, product_id: str, connection_id: str, idempotency_key: str | None = None):
    resolved_key = idempotency_key or f"{connection_id}:{product_id}:{uuid.uuid4().hex}"

    with transaction.atomic():
        existing = ProductSocialPostLog.objects.filter(
            shop_id=shop_id,
            idempotency_key=resolved_key,
            deleted_at__isnull=True,
        ).first()
        if existing:
            return existing, False

        log = ProductSocialPostLog.objects.create(
            tenant_id=shop_id,
            shop_id=shop_id,
            product_id=product_id,
            connection_id=connection_id,
            idempotency_key=resolved_key,
            status=SocialPostStatus.QUEUED,
        )
        return log, True
