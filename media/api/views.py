"""
Media API Views

Implements the two-step presigned upload flow:
  Step 1: GET /api/v1/media/upload-url/ → presigned PUT URL
  Step 2: POST /api/v1/media/confirm/   → confirms, queues WebP conversion

Authenticated endpoints — shop context injected via X-Tenant-ID header
(handled by TenantMiddleware).
"""
import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status, generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination

from media.api.serializers import (
    ConfirmUploadSerializer,
    MediaSerializer,
    PresignedUploadRequestSerializer,
    PresignedUploadResponseSerializer,
)
from media.models import Media
from media.services import confirm_media_upload, generate_presigned_upload_url

logger = logging.getLogger(__name__)


class PresignedUploadURLView(APIView):
    """
    GET /api/v1/media/upload-url/
    Returns a temporary S3/MinIO presigned PUT URL and the s3_key.
    The client uses this URL to upload the file directly (browser → S3).
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=PresignedUploadRequestSerializer,
        responses={200: PresignedUploadResponseSerializer},
        summary="Get presigned S3 upload URL",
        tags=["media"],
    )
    def get(self, request):
        shop_id = getattr(request, "tenant_id", None)
        if not shop_id:
            return Response(
                {"detail": "No shop context found. Provide X-Tenant-ID header."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = PresignedUploadRequestSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        try:
            result = generate_presigned_upload_url(
                shop_id=shop_id,
                filename=serializer.validated_data["filename"],
                content_type=serializer.validated_data["content_type"],
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result, status=status.HTTP_200_OK)


class ConfirmUploadView(APIView):
    """
    POST /api/v1/media/confirm/
    Called after a successful direct-to-S3 upload.
    Creates the Media DB record and enqueues WebP conversion.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=ConfirmUploadSerializer,
        responses={201: MediaSerializer},
        summary="Confirm upload and trigger WebP processing",
        tags=["media"],
    )
    def post(self, request):
        shop_id = getattr(request, "tenant_id", None)
        if not shop_id:
            return Response(
                {"detail": "No shop context found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ConfirmUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        media = confirm_media_upload(
            shop_id=shop_id,
            s3_key=serializer.validated_data["s3_key"],
            original_filename=serializer.validated_data["original_filename"],
            user_id=request.user.id,
        )

        return Response(MediaSerializer(media).data, status=status.HTTP_201_CREATED)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 18
    page_size_query_param = 'page_size'
    max_page_size = 100


class MediaListView(generics.ListAPIView):
    """
    GET /api/v1/media/
    List media for the current shop.
    """
    serializer_class = MediaSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    @extend_schema(
        responses={200: MediaSerializer(many=True)},
        summary="List media assets",
        tags=["media"],
    )
    def get_queryset(self):
        shop_id = getattr(self.request, "tenant_id", None)
        return Media.objects.filter(shop_id=shop_id, deleted_at__isnull=True).order_by('-created_at')


class MediaDeleteView(APIView):
    """
    DELETE /api/v1/media/{id}/
    Soft-deletes the Media record. Physical S3 deletion is deferred to Celery cleanup.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={204: None},
        summary="Soft-delete a media asset",
        tags=["media"],
    )
    def delete(self, request, media_id):
        shop_id = getattr(request, "tenant_id", None)
        try:
            media = Media.objects.get(
                id=media_id, shop_id=shop_id, deleted_at__isnull=True
            )
        except Media.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        media.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
