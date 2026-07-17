from __future__ import annotations

from django.db import transaction

from compliance.audit import audit_event_create
from orders.models import OrderStatus
from shipping.models import (
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
