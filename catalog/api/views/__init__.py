"""
Catalog API ViewSets — Dashboard (authenticated, shop-scoped)

All ViewSets follow the thin-controller pattern:
  - Permission and tenant validation
  - Delegate to service/selector layer
  - Return serialized response

Endpoints:
  /api/v1/catalog/categories/
  /api/v1/catalog/products/
  /api/v1/catalog/products/{id}/variants/
  /api/v1/catalog/products/{id}/variants/{vid}/adjust-stock/
  /api/v1/catalog/products/{id}/media/
  /api/v1/catalog/tracking-config/
"""
import logging

from django.core.paginator import Paginator
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

from catalog.api.serializers import (
    CategorySerializer,
    CategoryWriteSerializer,
    ProductDetailSerializer,
    ProductListSerializer,
    ProductWriteSerializer,
    ProductMediaSerializer,
    ProductVariantSerializer,
    ProductVariantWriteSerializer,
    ShopTrackingConfigSerializer,
    StockAdjustSerializer,
    ProductBulkUpdateItemSerializer,
    VariantBulkUpdateItemSerializer,
)
from catalog.models import ShopTrackingConfig
from catalog.selectors import (
    category_list_for_shop,
    product_get_by_id,
    product_list_for_dashboard,
    variant_list_for_product,
)
from catalog.services import (
    category_create,
    category_delete,
    category_update,
    product_archive,
    product_create,
    product_media_attach,
    product_media_reorder,
    product_publish,
    product_soft_delete,
    product_update,
    variant_create,
    variant_update_stock,
    variant_bulk_update,
    product_bulk_update,
)

logger = logging.getLogger(__name__)


def _require_tenant(request):
    """Extracts and validates the shop ID from the request context."""
    shop_id = getattr(request, "tenant_id", None)
    if not shop_id:
        raise PermissionError("No shop context. Provide X-Tenant-ID header.")
    return shop_id


# ─── Categories ──────────────────────────────────────────────────────────────

class CategoryViewSet(ViewSet):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: CategorySerializer(many=True)}, tags=["catalog"])
    def list(self, request):
        shop_id = _require_tenant(request)
        qs = category_list_for_shop(shop_id=shop_id)
        return Response(CategorySerializer(qs, many=True).data)

    @extend_schema(request=CategoryWriteSerializer, responses={201: CategorySerializer}, tags=["catalog"])
    def create(self, request):
        shop_id = _require_tenant(request)
        serializer = CategoryWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        category = category_create(
            shop_id=shop_id,
            user_id=request.user.id,
            name=data["name"],
            parent_id=str(data["parent_id"]) if data.get("parent_id") else None,
            sort_order=data.get("sort_order", 0),
        )
        return Response(CategorySerializer(category).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={200: CategorySerializer}, tags=["catalog"])
    def retrieve(self, request, pk=None):
        from catalog.models import Category
        shop_id = _require_tenant(request)
        try:
            cat = Category.objects.get(id=pk, shop_id=shop_id, deleted_at__isnull=True)
        except Category.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(CategorySerializer(cat).data)

    @extend_schema(request=CategoryWriteSerializer, responses={200: CategorySerializer}, tags=["catalog"])
    def partial_update(self, request, pk=None):
        shop_id = _require_tenant(request)
        serializer = CategoryWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            cat = category_update(
                category_id=pk, shop_id=shop_id, **serializer.validated_data
            )
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CategorySerializer(cat).data)

    @extend_schema(responses={204: None}, tags=["catalog"])
    def destroy(self, request, pk=None):
        shop_id = _require_tenant(request)
        category_delete(category_id=pk, shop_id=shop_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Products ────────────────────────────────────────────────────────────────

class ProductViewSet(ViewSet):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter("status", str, description="Filter by product status"),
            OpenApiParameter("category_id", str),
            OpenApiParameter("search", str),
            OpenApiParameter("page", int, default=1),
            OpenApiParameter("page_size", int, default=20),
        ],
        responses={200: ProductListSerializer(many=True)},
        tags=["catalog"],
    )
    def list(self, request):
        shop_id = _require_tenant(request)
        qs = product_list_for_dashboard(
            shop_id=shop_id,
            status=request.query_params.get("status"),
            category_id=request.query_params.get("category_id"),
            search=request.query_params.get("search"),
        )
        page_num = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 20))
        paginator = Paginator(qs, page_size)
        page = paginator.get_page(page_num)
        return Response({
            "count": paginator.count,
            "num_pages": paginator.num_pages,
            "results": ProductListSerializer(page.object_list, many=True).data,
        })

    @extend_schema(request=ProductWriteSerializer, responses={201: ProductDetailSerializer}, tags=["catalog"])
    def create(self, request):
        shop_id = _require_tenant(request)
        serializer = ProductWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            product = product_create(
                shop_id=shop_id,
                user_id=request.user.id,
                **serializer.validated_data,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            ProductDetailSerializer(product).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(responses={200: ProductDetailSerializer}, tags=["catalog"])
    def retrieve(self, request, pk=None):
        shop_id = _require_tenant(request)
        try:
            product = product_get_by_id(product_id=pk, shop_id=shop_id)
        except Exception:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(ProductDetailSerializer(product).data)

    @extend_schema(responses={200: ProductDetailSerializer}, tags=["catalog"])
    def partial_update(self, request, pk=None):
        shop_id = _require_tenant(request)
        serializer = ProductWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            product = product_update(
                product_id=pk, shop_id=shop_id, **serializer.validated_data
            )
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ProductDetailSerializer(product).data)

    @extend_schema(responses={204: None}, tags=["catalog"])
    def destroy(self, request, pk=None):
        shop_id = _require_tenant(request)
        product_soft_delete(product_id=pk, shop_id=shop_id)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(responses={200: ProductDetailSerializer}, tags=["catalog"])
    @action(detail=True, methods=["post"], url_path="publish")
    def publish(self, request, pk=None):
        shop_id = _require_tenant(request)
        try:
            product = product_publish(product_id=pk, shop_id=shop_id)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ProductDetailSerializer(product).data)

    @extend_schema(responses={200: ProductDetailSerializer}, tags=["catalog"])
    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        shop_id = _require_tenant(request)
        product = product_archive(product_id=pk, shop_id=shop_id)
        return Response(ProductDetailSerializer(product).data)

    @extend_schema(request=ProductBulkUpdateItemSerializer(many=True), responses={200: {"type": "object", "properties": {"updated_count": {"type": "integer"}}}}, tags=["catalog"])
    @action(detail=False, methods=["post"], url_path="bulk-update")
    def bulk_update(self, request):
        from catalog.api.serializers import ProductBulkUpdateItemSerializer
        shop_id = _require_tenant(request)
        serializer = ProductBulkUpdateItemSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        try:
            updated_count = product_bulk_update(
                shop_id=shop_id, updates=serializer.validated_data
            )
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"updated_count": updated_count}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="ai-generate-description")
    def ai_generate_description(self, request):
        """
        EPIC C-01: Generate an SEO-optimised product description using AI.
        Requires: name, specifications (dict), tone (string).
        """
        shop_id = _require_tenant(request)

        name = request.data.get("name")
        specs = request.data.get("specifications", {})
        tone = request.data.get("tone", "Professional")

        if not name:
            return Response({"detail": "Product name is required."}, status=status.HTTP_400_BAD_REQUEST)

        from ai.services import generate_description

        specs_str = "\n".join([f"{k}: {v}" for k, v in specs.items()])
        prompt = (
            f"Write a compelling, SEO-optimized product description for a product named '{name}'.\n"
            f"Tone: {tone}\n"
            f"Key Specifications:\n{specs_str}\n\n"
            "The description should be professional, highlighting benefits, and formatted with semantic HTML (p, ul, li) for a clean web storefront."
        )

        try:
            description = generate_description(shop_id=shop_id, prompt=prompt)
            return Response({"description": description})
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["post"], url_path="ai-generate-image")
    def ai_generate_image(self, request):
        """
        EPIC C-02: Generate a product image using AI (DALL-E 3) and save it.
        Requires: prompt (string).
        """
        shop_id = _require_tenant(request)
        prompt = request.data.get("prompt")

        if not prompt:
            return Response({"detail": "Prompt is required."}, status=status.HTTP_400_BAD_REQUEST)

        from ai.services import generate_image
        from media.services.ai_generate import save_ai_generated_image
        
        try:
            # 1. Generate via AI
            image_url = generate_image(shop_id=shop_id, prompt=prompt)
            
            # 2. Persist to our storage
            media = save_ai_generated_image(
                shop_id=shop_id,
                user_id=request.user.id,
                image_url=image_url
            )
            
            from media.api.serializers import MediaSerializer
            return Response({
                "media": MediaSerializer(media).data,
                "cdn_url": media.cdn_url
            })
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


# ─── Variants ────────────────────────────────────────────────────────────────

class VariantViewSet(ViewSet):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: ProductVariantSerializer(many=True)}, tags=["catalog"])
    def list(self, request, product_pk=None):
        shop_id = _require_tenant(request)
        qs = variant_list_for_product(product_id=product_pk, shop_id=shop_id)
        return Response(ProductVariantSerializer(qs, many=True).data)

    @extend_schema(request=ProductVariantWriteSerializer, responses={201: ProductVariantSerializer}, tags=["catalog"])
    def create(self, request, product_pk=None):
        shop_id = _require_tenant(request)
        serializer = ProductVariantWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            variant = variant_create(
                product_id=product_pk,
                shop_id=shop_id,
                user_id=request.user.id,
                **serializer.validated_data,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ProductVariantSerializer(variant).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={200: ProductVariantSerializer}, tags=["catalog"])
    def partial_update(self, request, product_pk=None, pk=None):
        from catalog.models import ProductVariant
        shop_id = _require_tenant(request)
        serializer = ProductVariantWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            variant = ProductVariant.objects.get(
                id=pk, product_id=product_pk, shop_id=shop_id, deleted_at__isnull=True
            )
            for field, value in serializer.validated_data.items():
                setattr(variant, field, value)
            variant.save()
        except ProductVariant.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(ProductVariantSerializer(variant).data)

    @extend_schema(request=StockAdjustSerializer, responses={200: ProductVariantSerializer}, tags=["catalog"])
    @action(detail=True, methods=["post"], url_path="adjust-stock")
    def adjust_stock(self, request, product_pk=None, pk=None):
        shop_id = _require_tenant(request)
        serializer = StockAdjustSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            variant = variant_update_stock(
                variant_id=pk,
                shop_id=shop_id,
                delta=serializer.validated_data["delta"],
                reason=serializer.validated_data["reason"],
                reference_id=serializer.validated_data.get("reference_id", ""),
                user_id=request.user.id,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ProductVariantSerializer(variant).data)

    @extend_schema(request=VariantBulkUpdateItemSerializer(many=True), responses={200: {"type": "object", "properties": {"updated_count": {"type": "integer"}}}}, tags=["catalog"])
    @action(detail=False, methods=["post"], url_path="bulk-update")
    def bulk_update(self, request, product_pk=None):
        from catalog.api.serializers import VariantBulkUpdateItemSerializer
        shop_id = _require_tenant(request)
        serializer = VariantBulkUpdateItemSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        try:
            updated_count = variant_bulk_update(
                shop_id=shop_id, user_id=request.user.id, updates=serializer.validated_data
            )
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"updated_count": updated_count}, status=status.HTTP_200_OK)


# ─── Tracking Config ────────────────────────────────────────────────────────

class TrackingConfigView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: ShopTrackingConfigSerializer}, tags=["catalog"])
    def get(self, request):
        shop_id = _require_tenant(request)
        config, _ = ShopTrackingConfig.objects.get_or_create(shop_id=shop_id)
        return Response(ShopTrackingConfigSerializer(config).data)

    @extend_schema(request=ShopTrackingConfigSerializer, responses={200: ShopTrackingConfigSerializer}, tags=["catalog"])
    def patch(self, request):
        shop_id = _require_tenant(request)

        # FeatureGate: Only Basic+ plans can configure tracking pixels
        from core.feature_gate import FeatureGate
        from shops.models import Shop
        shop = Shop.objects.select_related("plan").get(id=shop_id)
        if not FeatureGate.can_use_feature(shop, "can_use_pixels"):
            return Response(
                {"detail": "Tracking pixel configuration requires the Basic plan or higher."},
                status=status.HTTP_403_FORBIDDEN,
            )

        config, _ = ShopTrackingConfig.objects.get_or_create(shop_id=shop_id)
        serializer = ShopTrackingConfigSerializer(config, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
