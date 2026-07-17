from __future__ import annotations

from django.db import transaction

from compliance.audit import audit_event_create
from orders.models import (
    CourierConsignment,
    CourierConsignmentStatus,
    CourierProvider,
    Order,
    OrderStatus,
)
from orders.services.transitions import order_transition


COURIER_TO_ORDER_STATUS: dict[str, str] = {
    CourierConsignmentStatus.DISPATCHED: OrderStatus.SHIPPED,
    CourierConsignmentStatus.IN_TRANSIT: OrderStatus.IN_TRANSIT,
    CourierConsignmentStatus.DELIVERED: OrderStatus.DELIVERED,
    CourierConsignmentStatus.RTO: OrderStatus.RTO_RETURNED,
}


def courier_consignment_upsert(
    *,
    order: Order,
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
            "order": order,
            "shop": order.shop,
            "status": status,
            "tracking_code": tracking_code,
            "payload": payload or {},
        },
    )
    return consignment


def courier_apply_status_from_webhook(
    *,
    payload: dict,
    provider: str | None = None,
    actor_user_id: str | None = None,
) -> CourierConsignment:
    provider = str(provider or payload.get("provider") or CourierProvider.OTHER).upper()
    
    from orders.services.courier_registry import courier_registry
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

    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order_id, deleted_at__isnull=True)
        consignment = courier_consignment_upsert(
            order=order,
            provider=provider,
            external_consignment_id=external_consignment_id,
            status=status,
            tracking_code=tracking_code,
            payload=payload,
        )

        target_status = COURIER_TO_ORDER_STATUS.get(status)
        if target_status and target_status != order.status:
            try:
                order_transition(
                    order=order,
                    to_status=target_status,
                    actor_user_id=actor_user_id,
                    actor_role="MANAGER",
                    reason=f"Courier webhook status update: {status}",
                )
            except ValueError:
                order_transition(
                    order=order,
                    to_status=OrderStatus.ON_HOLD,
                    actor_user_id=actor_user_id,
                    actor_role="MANAGER",
                    reason=f"Courier webhook inconsistent transition from {order.status} with event {status}",
                )

        audit_event_create(
            shop_id=str(order.shop_id),
            actor_user_id=actor_user_id,
            action="COURIER_WEBHOOK_APPLIED",
            resource_type="orders.CourierConsignment",
            resource_id=str(consignment.id),
            metadata={
                "order_id": str(order.id),
                "provider": provider,
                "consignment_id": external_consignment_id,
                "status": status,
            },
        )
        
        # Phase 5: Ingest courier delivery-success-rate feed if present.
        if success_rate is not None:
            from identity.services.behavioral import _terminal_person_for_order, ingest_courier_success_rate
            person = _terminal_person_for_order(order)
            if person:
                try:
                    ingest_courier_success_rate(person_id=str(person.id), rate=float(success_rate))
                except (ValueError, TypeError):
                    pass

        return consignment

