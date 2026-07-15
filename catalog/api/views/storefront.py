"""
Storefront API Views — Public, no authentication required.

These endpoints serve the Next.js storefront Server Components.
They only return PUBLISHED products and are scoped by shop_slug in the URL.
"""
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.api.serializers import ProductDetailSerializer, ProductListSerializer
from shops.api.serializers import StoreThemeSerializer
from catalog.models import Product
from catalog.selectors import product_get_for_storefront, product_list_for_storefront
from shops.models import Shop
from core.services.rls import tenant_context
from rest_framework import serializers
from django.core.paginator import Paginator


class StorefrontShopSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shop
        fields = ["id", "name", "subdomain", "base_currency"]


def _get_shop_by_slug(shop_slug: str) -> Shop:
    return Shop.objects.get(subdomain=shop_slug, deleted_at__isnull=True)


class StorefrontProductListView(APIView):
    """GET /api/v1/storefront/{shop_slug}/products/"""

    authentication_classes = []
    permission_classes = []

    @extend_schema(
        responses={200: ProductListSerializer(many=True)},
        tags=["storefront"],
        summary="List published products for a storefront",
    )
    def get(self, request, shop_slug: str):
        try:
            shop = _get_shop_by_slug(shop_slug)
        except Shop.DoesNotExist:
            return Response({"detail": "Shop not found."}, status=404)

        with tenant_context(shop.id):
            qs = product_list_for_storefront(
                shop_id=str(shop.id),
                category_slug=request.query_params.get("category"),
            )

            # Simple cursor pagination for storefront
            page_size = min(int(request.query_params.get("page_size", 24)), 100)
            paginator = Paginator(qs, page_size)
            page = paginator.get_page(int(request.query_params.get("page", 1)))

        return Response({
            "count": paginator.count,
            "num_pages": paginator.num_pages,
            "results": ProductListSerializer(page.object_list, many=True).data,
        })


class StorefrontProductDetailView(APIView):
    """GET /api/v1/storefront/{shop_slug}/products/{slug}/"""

    authentication_classes = []
    permission_classes = []

    @extend_schema(
        responses={200: ProductDetailSerializer},
        tags=["storefront"],
        summary="Get published product detail for storefront",
    )
    def get(self, request, shop_slug: str, slug: str):
        try:
            shop = _get_shop_by_slug(shop_slug)
        except Shop.DoesNotExist:
            return Response({"detail": "Shop not found."}, status=404)

        try:
            with tenant_context(shop.id):
                product = product_get_for_storefront(
                    shop_id=str(shop.id), slug=slug
                )
        except Product.DoesNotExist:
            return Response({"detail": "Product not found."}, status=404)

        return Response(ProductDetailSerializer(product).data)


class StorefrontTrackingConfigView(APIView):
    """GET /api/v1/storefront/{shop_slug}/tracking/ — Used by Next.js layout server render."""

    authentication_classes = []
    permission_classes = []

    @extend_schema(tags=["storefront"])
    def get(self, request, shop_slug: str):
        try:
            shop = _get_shop_by_slug(shop_slug)
        except Shop.DoesNotExist:
            return Response({"detail": "Shop not found."}, status=404)

        from catalog.models import ShopTrackingConfig
        try:
            config = shop.tracking_config
        except ShopTrackingConfig.DoesNotExist:
            return Response({"fb_pixel_id": "", "ga4_measurement_id": "", "gtm_id": ""})

        from catalog.api.serializers import ShopTrackingConfigSerializer
        return Response(ShopTrackingConfigSerializer(config).data)


class StorefrontShopView(APIView):
    """GET /api/v1/storefront/{shop_slug}/config/ — Used to get shop name/currency."""

    authentication_classes = []
    permission_classes = []

    @extend_schema(responses={200: StorefrontShopSerializer}, tags=["storefront"])
    def get(self, request, shop_slug: str):
        try:
            shop = _get_shop_by_slug(shop_slug)
        except Shop.DoesNotExist:
            return Response({"detail": "Shop not found."}, status=404)

        return Response(StorefrontShopSerializer(shop).data)

class StorefrontThemeView(APIView):
    """GET /api/v1/storefront/{shop_slug}/theme/ — Used by Next.js layout server render."""

    authentication_classes = []
    permission_classes = []

    @extend_schema(responses={200: StoreThemeSerializer}, tags=["storefront"])
    def get(self, request, shop_slug: str):
        try:
            shop = _get_shop_by_slug(shop_slug)
        except Shop.DoesNotExist:
            return Response({"detail": "Shop not found."}, status=404)

        from shops.models import StoreTheme
        theme, _ = StoreTheme.objects.get_or_create(shop_id=shop.id)
        return Response(StoreThemeSerializer(theme).data)

