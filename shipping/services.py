from __future__ import annotations

import json

from django.db import transaction

from compliance.audit import audit_event_create
from orders.models import OrderStatus
from shipping.models import (
    CourierAccount,
    CourierConsignment,
    CourierConsignmentStatus,
    CourierProvider,
)
from shipping.registry import courier_registry


COURIER_TO_ORDER_STATUS: dict[str, str] = {
    CourierConsignmentStatus.DISPATCHED: OrderStatus.SHIPPED,
    CourierConsignmentStatus.IN_TRANSIT: OrderStatus.IN_TRANSIT,
    CourierConsignmentStatus.DELIVERED: OrderStatus.DELIVERED,
    CourierConsignmentStatus.RTO: OrderStatus.RTO_RETURNED,
}


def create_consignment(
    *,
    order_id: str,
    shop_id: str,
    provider: str,
    external_consignment_id: str,
    status: str,
    tracking_code: str = "",
    payload: dict | None = None,
) -> CourierConsignment:
    consignment, _ = CourierConsignment.objects.update_or_create(
        provider=provider,
        external_consignment_id=external_consignment_id,
        defaults={
            "order_id": order_id,
            "shop_id": shop_id,
            "status": status,
            "tracking_code": tracking_code,
            "payload": payload or {},
        },
    )
    return consignment


def get_consignment_status(order_id: str) -> str | None:
    consignment = CourierConsignment.objects.filter(order_id=order_id).order_by('-created_at').first()
    return consignment.status if consignment else None


def apply_status_from_webhook(
    *,
    payload: dict,
    provider: str | None = None,
    actor_user_id: str | None = None,
) -> CourierConsignment:
    provider = str(provider or payload.get("provider") or CourierProvider.OTHER).upper()
    
    courier_impl = courier_registry.get_provider(provider)
    
    if courier_impl:
        parsed_data = courier_impl.parse_webhook_payload(payload)
        external_consignment_id = str(parsed_data.get("external_consignment_id") or "")
        order_id = str(parsed_data.get("order_id") or "")
        status = str(parsed_data.get("status") or "").upper()
        tracking_code = str(parsed_data.get("tracking_code") or "")
        success_rate = parsed_data.get("success_rate")
    else:
        # Fallback to direct mapping for OTHER/unregistered providers
        external_consignment_id = str(payload.get("consignment_id") or payload.get("id") or "")
        order_id = str(payload.get("order_id") or "")
        status = str(payload.get("status") or "").upper()
        tracking_code = str(payload.get("tracking_code") or "")
        success_rate = payload.get("success_rate") or payload.get("delivery_success_rate")

    if not order_id or not external_consignment_id or not status:
        raise ValueError("order_id, consignment_id and status are required.")

    if status not in {choice.value for choice in CourierConsignmentStatus}:
        raise ValueError(f"Unsupported courier status: {status}")

    # To maintain decoupling, shipping doesn't directly import Order objects.
    # It queries orders.selectors for needed metadata.
    from orders.selectors.orders import get_order_shop_id
    shop_id = get_order_shop_id(order_id=order_id)
    if not shop_id:
        raise ValueError(f"Could not find valid order for ID {order_id}")

    with transaction.atomic():
        consignment = create_consignment(
            order_id=order_id,
            shop_id=shop_id,
            provider=provider,
            external_consignment_id=external_consignment_id,
            status=status,
            tracking_code=tracking_code,
            payload=payload,
        )

        target_status = COURIER_TO_ORDER_STATUS.get(status)
        if target_status:
            from orders.services.transitions import update_order_status_from_courier
            update_order_status_from_courier(
                order_id=order_id,
                new_status=target_status,
                meta={"actor_user_id": actor_user_id, "reason": f"Courier webhook status update: {status}"}
            )

        audit_event_create(
            shop_id=str(shop_id),
            actor_user_id=actor_user_id,
            action="COURIER_WEBHOOK_APPLIED",
            resource_type="shipping.CourierConsignment",
            resource_id=str(consignment.id),
            metadata={
                "order_id": order_id,
                "provider": provider,
                "consignment_id": external_consignment_id,
                "status": status,
            },
        )
        
        # Ingest courier delivery-success-rate feed if present.
        if success_rate is not None:
            from orders.selectors.orders import get_order_terminal_person_id
            from identity.services.behavioral import ingest_courier_success_rate
            person_id = get_order_terminal_person_id(order_id)
            if person_id:
                try:
                    ingest_courier_success_rate(person_id=str(person_id), rate=float(success_rate))
                except (ValueError, TypeError):
                    pass

        return consignment


# ─── Courier credentials (per-shop merchant accounts) ────────────────────────


def set_courier_credentials(
    *,
    shop_id: str,
    provider: str,
    credentials: dict,
    is_test_mode: bool = False,
    label: str = "",
    default_store_id: str = "",
) -> CourierAccount:
    """
    Store (or update) a shop's courier merchant credentials.

    Credentials are JSON-encoded into ``credentials_encrypted``. This is the only
    supported way to write courier credentials — never assign the field directly.
    """
    provider = str(provider).upper()
    account, _ = CourierAccount.objects.get_or_create(
        shop_id=shop_id,
        provider=provider,
        defaults={"tenant_id": shop_id},
    )
    account.credentials_encrypted = json.dumps(credentials)
    account.is_test_mode = is_test_mode
    if label:
        account.label = label
    if default_store_id:
        account.default_store_id = default_store_id
    account.save()
    return account


def get_courier_account(*, shop_id: str, provider: str) -> CourierAccount | None:
    provider = str(provider).upper()
    return (
        CourierAccount.objects.filter(shop_id=shop_id, provider=provider, is_active=True)
        .first()
    )


def get_courier_credentials(*, shop_id: str, provider: str) -> dict | None:
    """Return the decoded credential blob for a shop's active courier account."""
    account = get_courier_account(shop_id=shop_id, provider=provider)
    if not account or not account.credentials_encrypted:
        return None
    return json.loads(account.credentials_encrypted)


# ─── Inter-app courier API (consumed by orders, dashboard, etc.) ─────────────
#
# These keep shipping decoupled: order metadata is pulled through
# orders.selectors rather than by importing Order objects directly.


def create_shipment(*, order_id: str, provider: str, actor_user_id: str | None = None) -> CourierConsignment:
    """
    Book a shipment for an order with the given courier and persist the consignment.

    Raises ValueError if the order/provider is unusable or the provider is not
    registered.
    """
    provider = str(provider).upper()
    courier_impl = courier_registry.get_provider(provider)
    if not courier_impl:
        raise ValueError(f"No courier provider registered for: {provider}")

    from orders.selectors.orders import get_order_shipping_details
    details = get_order_shipping_details(order_id=order_id)
    if not details:
        raise ValueError(f"Could not resolve shipping details for order {order_id}")

    shop_id = str(details["shop_id"])
    result = courier_impl.create_consignment(shop_id, details)

    external_consignment_id = str(result.get("external_consignment_id") or "")
    status = str(result.get("status") or CourierConsignmentStatus.CREATED).upper()
    tracking_code = str(result.get("tracking_code") or "")
    if not external_consignment_id:
        raise ValueError("Courier did not return a consignment id.")
    if status not in {choice.value for choice in CourierConsignmentStatus}:
        status = CourierConsignmentStatus.CREATED

    with transaction.atomic():
        consignment = create_consignment(
            order_id=order_id,
            shop_id=shop_id,
            provider=provider,
            external_consignment_id=external_consignment_id,
            status=status,
            tracking_code=tracking_code,
            payload=result.get("payload") or {},
        )
        audit_event_create(
            shop_id=shop_id,
            actor_user_id=actor_user_id,
            action="COURIER_SHIPMENT_CREATED",
            resource_type="shipping.CourierConsignment",
            resource_id=str(consignment.id),
            metadata={
                "order_id": order_id,
                "provider": provider,
                "consignment_id": external_consignment_id,
            },
        )
    return consignment


def estimate_shipping_price(*, shop_id: str, provider: str, price_request: dict) -> dict:
    courier_impl = courier_registry.get_provider(str(provider).upper())
    if not courier_impl:
        raise ValueError(f"No courier provider registered for: {provider}")
    return courier_impl.calculate_price(shop_id, price_request)


def get_courier_locations(
    *, shop_id: str, provider: str, city_id: int | None = None, zone_id: int | None = None
) -> list[dict]:
    """Return cities, or zones for a city, or areas for a zone — depending on args."""
    courier_impl = courier_registry.get_provider(str(provider).upper())
    if not courier_impl:
        raise ValueError(f"No courier provider registered for: {provider}")
    if zone_id is not None:
        return courier_impl.list_areas(shop_id, zone_id)
    if city_id is not None:
        return courier_impl.list_zones(shop_id, city_id)
    return courier_impl.list_cities(shop_id)


def get_shipment_tracking(*, order_id: str) -> dict | None:
    """Fetch live tracking for an order's most recent consignment from the courier."""
    consignment = (
        CourierConsignment.objects.filter(order_id=order_id).order_by("-created_at").first()
    )
    if not consignment:
        return None
    courier_impl = courier_registry.get_provider(consignment.provider)
    if not courier_impl:
        return {
            "status": consignment.status,
            "tracking_code": consignment.tracking_code,
            "payload": consignment.payload,
        }
    return courier_impl.get_tracking(str(consignment.shop_id), consignment.external_consignment_id)
