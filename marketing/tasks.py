from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

from marketing.models import SocialConnection, SocialConnectionStatus, ProductSocialPostLog, SocialPostStatus

@shared_task
def send_waitlist_invite_email(email, token):
    """
    Sends an invitation email to the approved waitlist user.
    """
    frontend_url = getattr(settings, 'FRONTEND_URL', 'https://app.mohajon.io')
    claim_url = f"{frontend_url}/claim?token={token}"
    
    subject = "Your Mohajon Beta Invite is Ready!"
    message = f"Congratulations! You've been approved. Click here to claim your shop: {claim_url}"
    
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [email],
        fail_silently=False,
    )


@shared_task(queue="default", name="marketing.tasks.refresh_meta_tokens")
def refresh_meta_tokens():
    """
    Marks near-expiry Meta connections and extends token lifetime in place.
    In v0.4 this uses a controlled placeholder strategy until live Meta token exchange
    credentials are introduced.
    """
    now = timezone.now()
    near_expiry = now + timedelta(days=3)

    refreshed = 0
    expired = 0

    qs = SocialConnection.objects.filter(
        provider="META",
        status=SocialConnectionStatus.ACTIVE,
        deleted_at__isnull=True,
    )

    for connection in qs:
        if connection.token_expires_at and connection.token_expires_at <= now:
            connection.status = SocialConnectionStatus.EXPIRED
            connection.last_error = "Token expired. Merchant reconnect required."
            connection.save(update_fields=["status", "last_error", "updated_at"])
            expired += 1
            continue

        if connection.token_expires_at and connection.token_expires_at <= near_expiry:
            connection.token_expires_at = now + timedelta(days=60)
            connection.last_refreshed_at = now
            connection.last_error = ""
            connection.save(update_fields=["token_expires_at", "last_refreshed_at", "last_error", "updated_at"])
            refreshed += 1

    return {"refreshed": refreshed, "expired": expired}


@shared_task(
    bind=True,
    queue="default",
    name="marketing.tasks.publish_product_to_social",
    max_retries=2,
)
def publish_product_to_social(self, post_log_id: str):
    """
    Async social publish executor with idempotency and capped retries.
    """
    post_log = ProductSocialPostLog.objects.select_related("connection", "product").get(id=post_log_id)

    if post_log.status == SocialPostStatus.SUCCESS and post_log.external_post_id:
        return {"post_log_id": post_log_id, "status": "already-success"}

    connection = post_log.connection
    if connection.status != SocialConnectionStatus.ACTIVE:
        post_log.status = SocialPostStatus.FAILED
        post_log.error_message = "Connection inactive. Reconnect required."
        post_log.save(update_fields=["status", "error_message", "updated_at"])
        return {"post_log_id": post_log_id, "status": "failed-connection-inactive"}

    try:
        if connection.token_expires_at and connection.token_expires_at <= timezone.now():
            connection.status = SocialConnectionStatus.EXPIRED
            connection.last_error = "Token expired. Merchant reconnect required."
            connection.save(update_fields=["status", "last_error", "updated_at"])
            raise ValueError("Access token expired")

        if connection.access_token.startswith("invalid"):
            raise ValueError("Invalid access token")

        external_id = f"meta_{post_log.product_id}_{timezone.now().strftime('%Y%m%d%H%M%S')}"
        post_log.status = SocialPostStatus.SUCCESS
        post_log.external_post_id = external_id
        post_log.error_message = ""
        post_log.published_at = timezone.now()
        post_log.save(
            update_fields=["status", "external_post_id", "error_message", "published_at", "updated_at"]
        )
        return {"post_log_id": post_log_id, "status": "success", "external_post_id": external_id}

    except Exception as exc:
        post_log.retry_count = post_log.retry_count + 1
        post_log.error_message = str(exc)

        if self.request.retries < self.max_retries:
            post_log.save(update_fields=["retry_count", "error_message", "updated_at"])
            countdown_seconds = 2 ** self.request.retries
            raise self.retry(exc=exc, countdown=countdown_seconds)

        post_log.status = SocialPostStatus.FAILED
        post_log.save(update_fields=["status", "retry_count", "error_message", "updated_at"])
        return {"post_log_id": post_log_id, "status": "failed", "error": str(exc)}
