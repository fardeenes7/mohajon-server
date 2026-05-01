from rest_framework import serializers
from media.models import Media


class ProductLinkSerializer(serializers.Serializer):
    id = serializers.UUIDField(source="product.id")
    name = serializers.CharField(source="product.name")

class MediaSerializer(serializers.ModelSerializer):
    """Read serializer for Media objects returned to the client."""
    
    connected_products = ProductLinkSerializer(source="product_links", many=True, read_only=True)

    class Meta:
        model = Media
        fields = [
            "id",
            "original_filename",
            "cdn_url",
            "width",
            "height",
            "file_size",
            "mime_type",
            "processing_status",
            "created_at",
            "connected_products",
        ]
        read_only_fields = fields


class PresignedUploadRequestSerializer(serializers.Serializer):
    """Request body for GET /api/v1/media/upload-url/."""

    filename = serializers.CharField(max_length=512)
    content_type = serializers.CharField(max_length=127)

    def validate_content_type(self, value):
        allowed = {"image/jpeg", "image/png", "image/gif", "image/webp"}
        if value not in allowed:
            raise serializers.ValidationError(
                f"Content type '{value}' is not permitted. Allowed: {', '.join(allowed)}"
            )
        return value


class PresignedUploadResponseSerializer(serializers.Serializer):
    """Response shape for the presigned URL endpoint."""

    upload_url = serializers.URLField()
    s3_key = serializers.CharField()
    cdn_url = serializers.CharField()


class ConfirmUploadSerializer(serializers.Serializer):
    """Request body for POST /api/v1/media/confirm/."""

    s3_key = serializers.CharField(max_length=1024)
    original_filename = serializers.CharField(max_length=512)
