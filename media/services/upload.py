"""
Media upload service.

Implements the two-step presigned upload flow:
  1. GET /api/v1/media/upload-url/ → presigned S3 PUT URL + s3_key
  2. POST /api/v1/media/confirm/   → creates Media record + queues WebP conversion

This approach keeps all processing on the backend, avoiding double-compression
from simultaneous client-side Canvas + server-side conversion.
"""
import hashlib
import io
import uuid
from pathlib import Path
from typing import Any

import boto3
from django.conf import settings


def _get_s3_client():
    """
    Returns a boto3 S3 client configured for either real AWS S3 or local MinIO,
    driven entirely by env vars (AWS_S3_ENDPOINT_URL empty → real S3).
    """
    kwargs: dict[str, Any] = {
        "aws_access_key_id": settings.AWS_ACCESS_KEY_ID,
        "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY,
        "region_name": getattr(settings, "AWS_S3_REGION_NAME", "us-east-1"),
    }
    endpoint = getattr(settings, "AWS_S3_ENDPOINT_URL", None)
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.client("s3", **kwargs)


def generate_presigned_upload_url(
    *, shop_id: str, filename: str, content_type: str
) -> dict:
    """
    Generates a temporary signed URL for a client to PUT a file directly to S3/MinIO.
    Returns the s3_key so the client can confirm the upload afterwards.

    Security: the key is namespaced under the shop ID so tenants cannot overwrite
    each other's assets even if they guess the key format.
    """
    file_ext = Path(filename).suffix.lower()
    allowed_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    if file_ext not in allowed_extensions:
        raise ValueError(f"File type '{file_ext}' is not allowed.")

    unique_id = uuid.uuid4().hex
    # Objects always stored under tenant namespace
    s3_key = f"shops/{shop_id}/media/{unique_id}{file_ext}"

    client = _get_s3_client()
    upload_url = client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
            "Key": s3_key,
            "ContentType": content_type,
        },
        ExpiresIn=900,  # 15 minutes
    )

    cdn_base = getattr(settings, "CDN_BASE_URL", "").rstrip("/")
    cdn_url = f"{cdn_base}/{s3_key}" if cdn_base else ""

    return {
        "upload_url": upload_url,
        "s3_key": s3_key,
        "cdn_url": cdn_url,
    }


def confirm_media_upload(
    *, shop_id: str, s3_key: str, original_filename: str, user_id
) -> "Media":  # noqa: F821
    """
    Called after the client has successfully uploaded via the presigned URL.
    Creates the Media DB record and enqueues the WebP conversion Celery task.
    """
    from media.models import Media
    from media.tasks.processing import process_uploaded_media

    cdn_base = getattr(settings, "CDN_BASE_URL", "").rstrip("/")
    cdn_url = f"{cdn_base}/{s3_key}" if cdn_base else ""

    media = Media.objects.create(
        shop_id=shop_id,
        uploaded_by_id=user_id,
        original_filename=original_filename,
        s3_key=s3_key,
        cdn_url=cdn_url,
        processing_status=Media.ProcessingStatus.PENDING,
    )

    # Enqueue WebP conversion on the dedicated media queue
    process_uploaded_media.apply_async(
        kwargs={"media_id": str(media.id)},
        queue="media_processing",
    )

    return media
