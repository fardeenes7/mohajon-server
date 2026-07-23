from rest_framework import serializers
from catalog.models import Category, Product, ProductVariant, ProductMedia, ShopTrackingConfig
from media.models import Media


# ─── Media ─────────────────────────────────────────────────────────────────────

class MediaBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Media
        fields = ["id", "cdn_url", "width", "height", "processing_status", "original_filename"]
        read_only_fields = fields


# ─── Category ──────────────────────────────────────────────────────────────────

class CategorySerializer(serializers.ModelSerializer):
    children_count = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            "id", "name", "slug", "parent", "sort_order",
            "is_active", "children_count", "created_at",
        ]
        read_only_fields = ["id", "slug", "children_count", "created_at"]

    def get_children_count(self, obj) -> int:
        return obj.children.filter(deleted_at__isnull=True).count()


class CategoryWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    parent_id = serializers.UUIDField(required=False, allow_null=True)
    sort_order = serializers.IntegerField(default=0)
    is_active = serializers.BooleanField(default=True)


# ─── Variant ───────────────────────────────────────────────────────────────────

class ProductVariantSerializer(serializers.ModelSerializer):
    effective_price = serializers.ReadOnlyField()
    image = MediaBriefSerializer(read_only=True)

    class Meta:
        model = ProductVariant
        fields = [
            "id", "sku",
            "attribute_name_1", "attribute_value_1",
            "attribute_name_2", "attribute_value_2",
            "price_override", "weight_override_grams",
            "stock_quantity", "is_active",
            "effective_price", "image",
            "created_at",
        ]
        read_only_fields = ["id", "effective_price", "created_at"]


class ProductVariantWriteSerializer(serializers.Serializer):
    attribute_name_1 = serializers.CharField(max_length=50, default="")
    attribute_value_1 = serializers.CharField(max_length=100, default="")
    attribute_name_2 = serializers.CharField(max_length=50, default="")
    attribute_value_2 = serializers.CharField(max_length=100, default="")
    sku = serializers.CharField(max_length=120, default="", allow_blank=True)
    price_override = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True
    )
    weight_override_grams = serializers.IntegerField(required=False, allow_null=True)
    stock_quantity = serializers.IntegerField(default=0, min_value=0)
    image_id = serializers.UUIDField(required=False, allow_null=True)
    is_active = serializers.BooleanField(default=True)

class VariantBulkUpdateItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    price_override = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True
    )
    stock_quantity = serializers.IntegerField(required=False, min_value=0)
    is_active = serializers.BooleanField(required=False)


class StockAdjustSerializer(serializers.Serializer):
    delta = serializers.IntegerField(help_text="Positive = add stock, negative = reduce")
    reason = serializers.ChoiceField(choices=["RESTOCK", "ADJUSTMENT", "RETURN"])
    reference_id = serializers.CharField(default="", allow_blank=True)


# ─── Product Media ──────────────────────────────────────────────────────────────

class ProductMediaSerializer(serializers.ModelSerializer):
    media = MediaBriefSerializer(read_only=True)

    class Meta:
        model = ProductMedia
        fields = ["id", "media", "sort_order", "is_thumbnail"]
        read_only_fields = ["id"]


# ─── Product ───────────────────────────────────────────────────────────────────

class ProductListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views (dashboard table rows)."""
    thumbnail = serializers.SerializerMethodField()
    total_stock = serializers.ReadOnlyField()
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)

    class Meta:
        model = Product
        fields = [
            "id", "name", "slug", "sku", "status",
            "base_price", "compare_at_price", "tax_rate",
            "total_stock", "thumbnail", "category_name",
            "is_digital", "vector_status", "sort_order", "created_at",
        ]
        read_only_fields = fields

    def get_thumbnail(self, obj) -> str | None:
        thumb = obj.product_media.filter(is_thumbnail=True, deleted_at__isnull=True).first()
        if thumb and thumb.media:
            return thumb.media.cdn_url
        first_media = obj.product_media.filter(deleted_at__isnull=True).order_by("sort_order").first()
        return first_media.media.cdn_url if first_media and first_media.media else None


class ProductDetailSerializer(serializers.ModelSerializer):
    """Full detail serializer for create/edit views and storefront product pages."""
    variants = ProductVariantSerializer(many=True, read_only=True)
    product_media = ProductMediaSerializer(many=True, read_only=True)
    total_stock = serializers.ReadOnlyField()
    category = CategorySerializer(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id", "name", "slug", "sku",
            "status", "publish_at",
            "description",
            "base_price", "compare_at_price", "tax_rate",
            "weight_grams", "length_cm", "width_cm", "height_cm",
            "is_digital", "specifications",
            "seo_title", "seo_description",
            "sort_order", "total_stock", "vector_status",
            "category", "variants", "product_media",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "total_stock", "vector_status", "created_at", "updated_at"]


class ProductWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=500)
    slug = serializers.SlugField(max_length=500, required=False, allow_blank=True)
    category_id = serializers.UUIDField(required=False, allow_null=True)
    description = serializers.CharField(default="", allow_blank=True)
    status = serializers.ChoiceField(
        choices=["DRAFT", "PUBLISHED", "SCHEDULED", "ARCHIVED"],
        default="DRAFT",
    )
    publish_at = serializers.DateTimeField(required=False, allow_null=True)
    base_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    compare_at_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True
    )
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=4, default=0)
    sku = serializers.CharField(max_length=120, required=False, allow_blank=True)
    weight_grams = serializers.IntegerField(required=False, allow_null=True)
    length_cm = serializers.DecimalField(
        max_digits=8, decimal_places=2, required=False, allow_null=True
    )
    width_cm = serializers.DecimalField(
        max_digits=8, decimal_places=2, required=False, allow_null=True
    )
    height_cm = serializers.DecimalField(
        max_digits=8, decimal_places=2, required=False, allow_null=True
    )
    is_digital = serializers.BooleanField(default=False)
    specifications = serializers.DictField(default=dict)
    seo_title = serializers.CharField(max_length=120, default="", allow_blank=True)
    seo_description = serializers.CharField(max_length=320, default="", allow_blank=True)
    sort_order = serializers.IntegerField(default=0)
    media_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False
    )


class ProductBulkUpdateItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    base_price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    compare_at_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True
    )
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=4, required=False)
    status = serializers.ChoiceField(
        choices=["DRAFT", "PUBLISHED", "SCHEDULED", "ARCHIVED"], required=False
    )


# ─── Tracking Config ────────────────────────────────────────────────────────────

class ShopTrackingConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopTrackingConfig
        fields = ["id", "fb_pixel_id", "ga4_measurement_id", "gtm_id", "updated_at"]
        read_only_fields = ["id", "updated_at"]
