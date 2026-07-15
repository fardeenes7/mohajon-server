"""
Catalog selectors — read-only query layer.

No business logic here. Pure ORM queries optimised with select_related/
prefetch_related to avoid N+1 problems.

Search architecture (Fix 6.8 — post_v03_debrief.md):
  - Dashboard product list: Postgres FTS (status/category/shop filters; no typo tolerance needed)
  - Storefront search: Postgres FTS
"""
import logging

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import QuerySet

logger = logging.getLogger(__name__)


def product_count_active(*, shop_id: str) -> int:
    """Returns the count of non-deleted, non-archived products for a shop."""
    from catalog.models import Product

    return Product.objects.filter(
        shop_id=shop_id,
        deleted_at__isnull=True,
    ).exclude(status="ARCHIVED").count()


def product_list_for_dashboard(
    *,
    shop_id: str,
    status: str | None = None,
    category_id: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> QuerySet:
    """
    Returns a queryset of Products for the seller dashboard.
    Supports status filter, category filter, and full-text search via Postgres FTS.

    Note: Dashboard search uses Postgres FTS.
    """
    from catalog.models import Product

    qs = (
        Product.objects.filter(shop_id=shop_id, deleted_at__isnull=True)
        .select_related("category")
        .prefetch_related("product_media__media", "variants")
        .order_by("sort_order", "-created_at")
    )

    if status:
        qs = qs.filter(status=status)
    if category_id:
        qs = qs.filter(category_id=category_id)
    if search:
        query = SearchQuery(search, config="english")
        qs = (
            qs.annotate(rank=SearchRank("search_vector", query))
            .filter(search_vector=query)
            .order_by("-rank")
        )

    return qs


def product_get_by_id(*, product_id: str, shop_id: str) -> "Product":  # noqa: F821
    from catalog.models import Product

    return (
        Product.objects.select_related("category", "shop__tracking_config")
        .prefetch_related("product_media__media", "variants__image")
        .get(id=product_id, shop_id=shop_id, deleted_at__isnull=True)
    )


def product_list_for_storefront(
    *,
    shop_id: str,
    category_slug: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> QuerySet:
    """
    Returns only PUBLISHED products for the public storefront.
    Orders by manual sort_order (drag-drop configured by merchant).

    Fix 6.8: Uses Postgres FTS for storefront search.
    """
    from catalog.models import Product

    base_qs = (
        Product.objects.filter(
            shop_id=shop_id,
            status__in=["PUBLISHED", "OUT_OF_STOCK"],
            deleted_at__isnull=True,
        )
        .select_related("category")
        .prefetch_related(
            "product_media__media",
            "variants",
        )
        .order_by("sort_order", "-created_at")
    )

    if category_slug:
        base_qs = base_qs.filter(category__slug=category_slug)

    if search:
        fts_query = SearchQuery(search, config="english")
        base_qs = (
            base_qs.annotate(rank=SearchRank("search_vector", fts_query))
            .filter(search_vector=fts_query)
            .order_by("-rank")
        )

    return base_qs





def product_get_for_storefront(*, shop_id: str, slug: str) -> "Product":  # noqa: F821
    """
    Returns a single PUBLISHED product for the storefront by slug.
    Raises Product.DoesNotExist if not found or not published.
    """
    from catalog.models import Product

    return (
        Product.objects.select_related("category")
        .prefetch_related(
            "product_media__media",
            "variants__image",
        )
        .get(
            shop_id=shop_id,
            slug=slug,
            status__in=["PUBLISHED", "OUT_OF_STOCK"],
            deleted_at__isnull=True,
        )
    )


def category_list_for_shop(*, shop_id: str) -> QuerySet:
    from catalog.models import Category

    return Category.objects.filter(
        shop_id=shop_id, deleted_at__isnull=True, is_active=True
    ).select_related("parent").order_by("sort_order", "name")


def variant_list_for_product(*, product_id: str, shop_id: str) -> QuerySet:
    from catalog.models import ProductVariant

    return ProductVariant.objects.filter(
        product_id=product_id,
        shop_id=shop_id,
        deleted_at__isnull=True,
    ).select_related("image").order_by("attribute_value_1", "attribute_value_2")

