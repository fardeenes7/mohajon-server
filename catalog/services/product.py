"""
Product & Category service layer.

All business logic lives here. ViewSets are thin and delegate to these functions.
Cross-app communication uses only PKs/UUIDs, never model instances.

Key rules enforced:
- FeatureGate: max_products limit check before create
- Max 25 variants per product
- Max 2 attribute levels per variant
- SKU auto-generation if not provided
- InventoryLog written on every stock change
"""
import logging
from typing import Any

from django.db import transaction
from django.utils.text import slugify

logger = logging.getLogger(__name__)

MAX_VARIANTS_PER_PRODUCT = 25
MAX_ATTRIBUTE_LEVELS = 2


# ─── Category ──────────────────────────────────────────────────────────────────

def category_create(*, shop_id: str, user_id, name: str, parent_id: str | None = None, sort_order: int = 0) -> "Category":  # noqa: F821
    from catalog.models import Category

    slug = slugify(name)
    return Category.objects.create(
        shop_id=shop_id,
        tenant_id=shop_id,
        parent_id=parent_id,
        name=name,
        slug=slug,
        sort_order=sort_order,
    )


def category_update(*, category_id: str, shop_id: str, **data) -> "Category":  # noqa: F821
    from catalog.models import Category

    category = Category.objects.get(id=category_id, shop_id=shop_id, deleted_at__isnull=True)
    for field, value in data.items():
        setattr(category, field, value)
    if "name" in data and not data.get("slug"):
        category.slug = slugify(data["name"])
    category.save()
    return category


def category_delete(*, category_id: str, shop_id: str):
    from catalog.models import Category
    from django.utils import timezone

    Category.objects.filter(id=category_id, shop_id=shop_id).update(deleted_at=timezone.now())


# ─── Product ───────────────────────────────────────────────────────────────────

def product_create(*, shop_id: str, user_id, **data) -> "Product":  # noqa: F821
    """
    Creates a new product, enforcing FeatureGate product limit.
    Auto-generates SKU if not provided.
    """
    from catalog.models import Product
    from catalog.selectors.product import product_count_active
    from catalog.services.sku import ensure_unique_sku, generate_sku
    from core.feature_gate import FeatureGate
    from shops.models import Shop

    shop = Shop.objects.select_related("plan").get(id=shop_id)
    current_count = product_count_active(shop_id=shop_id)
    if not FeatureGate.check_limit(shop, "max_products", current_count):
        raise ValueError(
            f"Product limit reached. Upgrade your plan to add more products."
        )

    with transaction.atomic():
        media_ids = data.pop("media_ids", [])
        product = Product(
            shop_id=shop_id,
            tenant_id=shop_id,
            **{k: v for k, v in data.items() if k != "sku"},
        )
        if not product.slug:
            product.slug = slugify(product.name)

        product.save()

        # ── Attach Media ──────────────────────────────────────────────────
        if media_ids:
            for index, m_id in enumerate(media_ids):
                product_media_attach(
                    product_id=str(product.id),
                    shop_id=shop_id,
                    media_id=str(m_id),
                    sort_order=index,
                    is_thumbnail=(index == 0)
                )

        # ── Auto-generate SKU if not provided ─────────────────────────────
        requested_sku = data.get("sku", "").strip()
        if not requested_sku:
            candidate = generate_sku(shop_id=shop_id, product_id=str(product.id))
            product.sku = ensure_unique_sku(shop_id=shop_id, candidate_sku=candidate)
        else:
            product.sku = ensure_unique_sku(
                shop_id=shop_id, candidate_sku=requested_sku
            )
        product.save(update_fields=["sku"])

    return product


def product_update(*, product_id: str, shop_id: str, **data) -> "Product":  # noqa: F821
    from catalog.models import Product, ProductMedia
    import logging

    logger = logging.getLogger(__name__)

    with transaction.atomic():
        product = Product.objects.get(id=product_id, shop_id=shop_id, deleted_at__isnull=True)
        
        if "media_ids" in data:
            media_ids = data.pop("media_ids")
            if media_ids is not None:
                # Synchronize media: soft-delete existing links and re-attach new ones
                logger.info(f"Syncing {len(media_ids)} media items for product {product.id}")
                product.product_media.all().delete()
                
                # Create new links in order
                new_links = [
                    ProductMedia(
                        product=product,
                        media_id=m_id,
                        shop_id=shop_id,
                        tenant_id=shop_id,
                        sort_order=index,
                        is_thumbnail=(index == 0)
                    )
                    for index, m_id in enumerate(media_ids)
                ]
                ProductMedia.objects.bulk_create(new_links)

        # Update other fields, avoiding structural ones
        for field, value in data.items():
            if hasattr(product, field) and field not in ["id", "shop", "tenant_id"]:
                setattr(product, field, value)
        
        product.save()

    return product


def product_bulk_update(*, shop_id: str, updates: list[dict]) -> int:
    from catalog.models import Product

    with transaction.atomic():
        updated_count = 0
        for data in updates:
            product_id = data.pop("id", None)
            if not product_id:
                continue
            try:
                product = Product.objects.get(id=product_id, shop_id=shop_id, deleted_at__isnull=True)
                for field, value in data.items():
                    setattr(product, field, value)
                product.save()
                updated_count += 1
            except Product.DoesNotExist:
                continue
    return updated_count


def product_publish(*, product_id: str, shop_id: str) -> "Product":  # noqa: F821
    """Moves a product to PUBLISHED state. Validates at least one active variant exists."""
    from catalog.models import Product, ProductStatus

    product = Product.objects.get(id=product_id, shop_id=shop_id, deleted_at__isnull=True)
    if not product.variants.filter(is_active=True, deleted_at__isnull=True).exists():
        raise ValueError("Cannot publish a product with no active variants.")
    product.status = ProductStatus.PUBLISHED
    product.save(update_fields=["status"])
    return product


def product_archive(*, product_id: str, shop_id: str) -> "Product":  # noqa: F821
    from catalog.models import Product, ProductStatus

    product = Product.objects.get(id=product_id, shop_id=shop_id, deleted_at__isnull=True)
    product.status = ProductStatus.ARCHIVED
    product.save(update_fields=["status"])
    return product


def product_soft_delete(*, product_id: str, shop_id: str):
    from catalog.models import Product
    from django.utils import timezone

    Product.objects.filter(id=product_id, shop_id=shop_id).update(deleted_at=timezone.now())


# ─── Variant ───────────────────────────────────────────────────────────────────

def variant_create(*, product_id: str, shop_id: str, user_id, **data) -> "ProductVariant":  # noqa: F821
    """
    Creates a variant for a product. Enforces:
    - Max 25 variants per product
    - Max 2 attribute levels
    - Auto-generates SKU if not provided
    """
    from catalog.models import ProductVariant
    from catalog.services.sku import ensure_unique_sku, generate_sku

    # Enforce attribute level constraint
    attr2_name = data.get("attribute_name_2", "")
    attr1_name = data.get("attribute_name_1", "")
    if attr2_name and not attr1_name:
        raise ValueError("attribute_name_1 must be set before attribute_name_2.")

    with transaction.atomic():
        active_count = ProductVariant.objects.filter(
            product_id=product_id, deleted_at__isnull=True
        ).count()
        if active_count >= MAX_VARIANTS_PER_PRODUCT:
            raise ValueError(
                f"Max {MAX_VARIANTS_PER_PRODUCT} variants per product reached."
            )

        variant = ProductVariant(
            product_id=product_id,
            shop_id=shop_id,
            tenant_id=shop_id,
            **{k: v for k, v in data.items() if k != "sku"},
        )
        variant.save()

        requested_sku = data.get("sku", "").strip()
        if not requested_sku:
            candidate = generate_sku(
                shop_id=shop_id,
                product_id=product_id,
                variant_id=str(variant.id),
            )
            variant.sku = ensure_unique_sku(shop_id=shop_id, candidate_sku=candidate)
        else:
            variant.sku = ensure_unique_sku(
                shop_id=shop_id, candidate_sku=requested_sku
            )
        variant.save(update_fields=["sku"])

        # ── Initialize StockRecord for Default Location ──────────────────
        # TODO: After MVP, we might want to initialize records for all active locations
        from shops.models import StockLocation
        from catalog.models import StockRecord
        default_loc = StockLocation.objects.filter(shop_id=shop_id, is_default=True).first()
        if default_loc:
            StockRecord.objects.get_or_create(
                shop_id=shop_id,
                tenant_id=shop_id,
                variant=variant,
                location=default_loc,
                defaults={'quantity': data.get('stock_quantity', 0)}
            )

    return variant


def variant_update_stock(
    *,
    variant_id: str,
    shop_id: str,
    delta: int,
    reason: str,
    reference_id: str = "",
    user_id,
) -> "ProductVariant":  # noqa: F821
    """
    Atomically adjusts variant stock and writes an InventoryLog entry.
    Prevents stock from going below zero at the specific location.
    """
    from catalog.models import InventoryLog, ProductVariant, StockRecord
    from shops.models import ShopMember, StockLocation

    with transaction.atomic():
        variant = ProductVariant.objects.select_for_update().get(
            id=variant_id, shop_id=shop_id, deleted_at__isnull=True
        )
        
        # Determine target location: User's assigned location or Shop's default
        # TODO: Support explicit location_id in the API after MVP launch
        target_location = None
        if user_id:
            member = ShopMember.objects.filter(shop_id=shop_id, user_id=user_id).first()
            if member and member.location_id:
                target_location = member.location
        
        if not target_location:
            target_location = StockLocation.objects.filter(shop_id=shop_id, is_default=True).first()

        if not target_location:
            raise ValueError("No valid stock location found for this shop.")

        # Adjust specific StockRecord
        stock_record, _ = StockRecord.objects.select_for_update().get_or_create(
            shop_id=shop_id,
            tenant_id=shop_id,
            variant=variant,
            location=target_location
        )
        
        new_qty = stock_record.quantity + delta
        if new_qty < 0:
            raise ValueError(
                f"Insufficient stock at {target_location.name}. "
                f"Current: {stock_record.quantity}, attempted delta: {delta}"
            )
        
        stock_record.quantity = new_qty
        stock_record.save(update_fields=["quantity", "updated_at"])

        # Also update the 'legacy' global field for MVP compatibility
        # TODO: Remove variant.stock_quantity after full migration to StockRecord
        variant.stock_quantity = variant.global_stock
        variant.save(update_fields=["stock_quantity", "updated_at"])

        InventoryLog.objects.create(
            shop_id=shop_id,
            variant=variant,
            delta=delta,
            reason=reason,
            reference_id=reference_id,
            created_by_id=user_id,
            # TODO: Add location_id to InventoryLog after MVP
        )

    return variant


def variant_bulk_update(
    *,
    shop_id: str,
    user_id,
    updates: list[dict]
) -> int:
    from catalog.models import ProductVariant, InventoryLog

    with transaction.atomic():
        updated_count = 0
        for data in updates:
            variant_id = data.pop("id", None)
            if not variant_id:
                continue
            try:
                # Use select_for_update if we are touching stock_quantity
                variant = ProductVariant.objects.select_for_update().get(
                    id=variant_id, shop_id=shop_id, deleted_at__isnull=True
                )
                
                # If stock_quantity is being changed explicitly via bulk edit
                new_stock = data.pop("stock_quantity", None)
                if new_stock is not None:
                    # Determine target location
                    from shops.models import ShopMember, StockLocation
                    from catalog.models import StockRecord

                    target_location = None
                    if user_id:
                        member = ShopMember.objects.filter(shop_id=shop_id, user_id=user_id).first()
                        if member and member.location_id:
                            target_location = member.location
                    
                    if not target_location:
                        target_location = StockLocation.objects.filter(shop_id=shop_id, is_default=True).first()

                    if target_location:
                        stock_record, _ = StockRecord.objects.select_for_update().get_or_create(
                            shop_id=shop_id,
                            tenant_id=shop_id,
                            variant=variant,
                            location=target_location
                        )
                        delta = new_stock - stock_record.quantity
                        if delta != 0:
                            stock_record.quantity = new_stock
                            stock_record.save(update_fields=["quantity", "updated_at"])
                            
                            # Sync legacy field
                            variant.stock_quantity = variant.global_stock
                            
                            InventoryLog.objects.create(
                                shop_id=shop_id,
                                variant=variant,
                                delta=delta,
                                reason="BULK_ADJUSTMENT",
                                reference_id="bulk_edit",
                                created_by_id=user_id,
                            )

                for field, value in data.items():
                    setattr(variant, field, value)
                variant.save()
                updated_count += 1
            except ProductVariant.DoesNotExist:
                continue
    return updated_count


# ─── Product Media ──────────────────────────────────────────────────────────────

def product_media_attach(*, product_id: str, shop_id: str, media_id: str, sort_order: int = 0, is_thumbnail: bool = False) -> "ProductMedia":  # noqa: F821
    from catalog.models import ProductMedia

    if is_thumbnail:
        # Unset existing thumbnail first
        ProductMedia.objects.filter(
            product_id=product_id, is_thumbnail=True, deleted_at__isnull=True
        ).update(is_thumbnail=False)

    return ProductMedia.objects.create(
        product_id=product_id,
        shop_id=shop_id,
        tenant_id=shop_id,
        media_id=media_id,
        sort_order=sort_order,
        is_thumbnail=is_thumbnail,
    )


def product_media_reorder(*, product_id: str, shop_id: str, ordered_media_ids: list[str]):
    from catalog.models import ProductMedia

    for index, media_id in enumerate(ordered_media_ids):
        ProductMedia.objects.filter(
            product_id=product_id, media_id=media_id, shop_id=shop_id
        ).update(sort_order=index)
