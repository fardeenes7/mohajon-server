from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from django.db import transaction

from catalog.models import Product, ProductVariant
from orders.models import Order, OrderItem, OrderStatus, VerificationMethod
from orders.services.reservations import reservation_expires_at, reservation_store, reserve_stock_atomic
from shops.models import Shop, ShopSettings


def _resolve_line_price(*, product: Product, variant: ProductVariant | None) -> Decimal:
    if variant and variant.price_override is not None:
        return Decimal(str(variant.price_override))
    return Decimal(str(product.base_price))


def _resolve_available_stock(*, product: Product, variant: ProductVariant | None) -> int:
    if variant is not None:
        return int(variant.stock_quantity)
    return int(product.total_stock)


def _validate_prepaid_misuse_guard(*, shop: Shop, settings_obj: ShopSettings | None, payment_method: str | None) -> None:
    if payment_method != Order.PAYMENT_METHOD_PREPAID:
        return

    if not settings_obj or not getattr(settings_obj, "uses_platform_courier_credentials", False):
        return

    limit = int(getattr(settings_obj, "prepaid_misuse_consecutive_limit", 0) or 0)
    if limit <= 0:
        return

    recent_methods = (
        Order.objects.filter(shop=shop, deleted_at__isnull=True)
        .order_by("-created_at")
        .values_list("payment_method", flat=True)[:limit]
    )

    consecutive_prepaid = 0
    for method in recent_methods:
        if method == Order.PAYMENT_METHOD_PREPAID:
            consecutive_prepaid += 1
            continue
        break

    if consecutive_prepaid >= limit:
        raise ValueError(
            "Prepaid order creation is temporarily blocked due to consecutive prepaid activity while using platform courier credentials."
        )


def checkout_create_order(
    *,
    shop_id: str,
    items: Sequence[dict],
    customer_profile_id: str | None = None,
    payment_method: str | None = None,
    actor_user_id: str | None = None,
    phone_identity_id: str | None = None,
    actor_reference: str | None = None,
    is_verified: bool = False,
    verification_method: str | None = None,
) -> Order:
    shop = Shop.objects.get(id=shop_id, deleted_at__isnull=True)
    try:
        settings_obj = shop.settings
    except ShopSettings.DoesNotExist:
        settings_obj = None

    normalized_payment_method = (payment_method or "").upper() or None
    if normalized_payment_method not in {None, Order.PAYMENT_METHOD_COD, Order.PAYMENT_METHOD_PREPAID}:
        raise ValueError("Unsupported payment method.")

    _validate_prepaid_misuse_guard(
        shop=shop,
        settings_obj=settings_obj,
        payment_method=normalized_payment_method,
    )

    reservation_minutes = getattr(settings_obj, "stock_reservation_minutes", 30)
    expiry_at = reservation_expires_at(minutes=reservation_minutes)
    subtotal = Decimal("0.00")

    with transaction.atomic():
        # Resolve PhoneIdentity if missing but customer profile is present
        if not phone_identity_id and customer_profile_id:
            from shops.models import CustomerProfile
            from users.models import PhoneIdentity
            
            try:
                cp = CustomerProfile.objects.get(id=customer_profile_id)
                # Ensure a global PhoneIdentity exists for this customer
                phone_identity, _ = PhoneIdentity.objects.get_or_create(
                    phone_number=cp.phone_number,
                    defaults={"is_verified": False}
                )
                phone_identity_id = str(phone_identity.id)
            except CustomerProfile.DoesNotExist:
                pass

        order = Order.objects.create(
            shop=shop,
            tenant_id=shop.id,
            customer_profile_id=customer_profile_id,
            user_id=actor_user_id,
            phone_identity_id=phone_identity_id,
            status=OrderStatus.AWAITING_PAYMENT,
            subtotal_amount=Decimal("0.00"),
            shipping_amount=Decimal("0.00"),
            discount_amount=Decimal("0.00"),
            total_amount=Decimal("0.00"),
            currency=shop.base_currency,
            payment_method=normalized_payment_method,
            lock_expires_at=expiry_at,
            actor_reference=actor_reference or "",
            is_verified=is_verified,
            verification_method=verification_method or VerificationMethod.NONE,
        )

        for raw_item in items:
            product_id = raw_item.get("product_id")
            variant_id = raw_item.get("variant_id")
            quantity = int(raw_item.get("quantity", 1))
            if quantity <= 0:
                raise ValueError("Quantity must be positive.")

            product = Product.objects.select_related("shop").get(id=product_id, shop_id=shop.id, deleted_at__isnull=True)
            variant = None
            if variant_id:
                variant = ProductVariant.objects.select_related("product", "shop").get(
                    id=variant_id,
                    shop_id=shop.id,
                    product_id=product.id,
                    deleted_at__isnull=True,
                )

            available_stock = _resolve_available_stock(product=product, variant=variant)
            if quantity > available_stock:
                raise ValueError(
                    f"Insufficient stock for product {product.id}; requested {quantity}, available {available_stock}."
                )

            if not reserve_stock_atomic(str(product.id), str(variant.id) if variant else None, quantity):
                raise ValueError(
                    f"Failed to reserve stock for product {product.id}; requested {quantity}."
                )

            unit_price = _resolve_line_price(product=product, variant=variant)
            line_total = unit_price * Decimal(quantity)
            subtotal += line_total

            OrderItem.objects.create(
                order=order,
                tenant_id=order.tenant_id,
                product=product,
                variant=variant,
                quantity=quantity,
                unit_price=unit_price,
                line_discount_amount=Decimal("0.00"),
                line_total_amount=line_total,
            )

        order.subtotal_amount = subtotal
        order.total_amount = subtotal

        if order.phone_identity_id:
            from fraud.services.fraud_scoring import calculate_order_confidence

            order.confidence_level = calculate_order_confidence(order)

        order.save(
            update_fields=[
                "subtotal_amount",
                "total_amount",
                "lock_expires_at",
                "updated_at",
                "confidence_level",
                "is_verified",
                "verification_method",
            ]
        )

        from fraud.models import FraudEventType
        from fraud.services.fraud_scoring import dispatch_fraud_event

        dispatch_fraud_event(order, event_type=FraudEventType.ORDER_CREATED)

        reservation_store(order=order, minutes=reservation_minutes)

    return order
