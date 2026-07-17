import logging

from django.core.exceptions import PermissionDenied
from django.db import transaction

from compliance.audit import audit_event_create
from orders.models import Order, OrderStatus, OrderTransitionLog

logger = logging.getLogger(__name__)


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"AWAITING_PAYMENT", "CANCELLED", "ON_HOLD"},
    "AWAITING_PAYMENT": {"CONFIRMED", "CANCELLED", "ON_HOLD"},
    "CONFIRMED": {"PROCESSING", "CANCELLED", "ON_HOLD"},
    "PROCESSING": {"SHIPPED", "CANCELLED", "ON_HOLD"},
    "SHIPPED": {"IN_TRANSIT", "CANCELLED", "ON_HOLD"},
    "IN_TRANSIT": {"DELIVERED", "RTO_RETURNED", "CANCELLED", "ON_HOLD"},
    "DELIVERED": {"REFUNDED", "ON_HOLD"},
    "RTO_RETURNED": {"CONFIRMED", "REFUNDED", "ON_HOLD"},
    "ON_HOLD": {
        "PENDING",
        "AWAITING_PAYMENT",
        "CONFIRMED",
        "PROCESSING",
        "SHIPPED",
        "IN_TRANSIT",
        "DELIVERED",
        "CANCELLED",
        "REFUNDED",
        "RTO_RETURNED",
    },
}


ROLE_RESTRICTED_TARGETS: dict[str, set[str]] = {
    "CASHIER": {"REFUNDED", "RTO_RETURNED"},
    "INVENTORY_MANAGER": {"REFUNDED"},
}


def _assert_valid_status(to_status: str) -> None:
    valid_statuses = {choice.value for choice in OrderStatus}
    if to_status not in valid_statuses:
        raise ValueError(f"Unknown order status: {to_status}")


def _assert_role_allowed(*, actor_role: str | None, to_status: str) -> None:
    if not actor_role:
        return
    restricted_targets = ROLE_RESTRICTED_TARGETS.get(actor_role, set())
    if to_status in restricted_targets:
        raise PermissionDenied(f"Role {actor_role} cannot transition order to {to_status}.")


def order_transition(
    *,
    order: Order,
    to_status: str,
    actor_user_id: str | None = None,
    actor_role: str | None = None,
    reason: str = '',
) -> Order:
    _assert_valid_status(to_status)
    current_status = str(order.status)
    if current_status == to_status:
        return order

    next_allowed = ALLOWED_TRANSITIONS.get(current_status, set())
    if to_status not in next_allowed:
        raise ValueError(f"Illegal order transition: {current_status} -> {to_status}")

    _assert_role_allowed(actor_role=actor_role, to_status=to_status)

    order.status = to_status
    order.save(update_fields=["status", "updated_at"])
    OrderTransitionLog.objects.create(
        order=order,
        from_status=current_status,
        to_status=to_status,
        actor_user_id=actor_user_id,
        reason=reason,
    )

    audit_event_create(
        shop_id=str(order.shop_id),
        actor_user_id=actor_user_id,
        action="ORDER_STATUS_TRANSITION",
        resource_type="orders.Order",
        resource_id=str(order.id),
        metadata={
            "from_status": current_status,
            "to_status": to_status,
            "reason": reason,
            "actor_role": actor_role,
        },
    )

    if to_status in {OrderStatus.CANCELLED, OrderStatus.RTO_RETURNED}:
        from fraud.models import FraudEventType
        from fraud.services.fraud_scoring import dispatch_fraud_event

        if to_status == OrderStatus.CANCELLED:
            dispatch_fraud_event(order, event_type=FraudEventType.ORDER_CANCELLED, base_penalty=2)
        if to_status == OrderStatus.RTO_RETURNED:
            dispatch_fraud_event(order, event_type=FraudEventType.DELIVERY_FAILED, base_penalty=20)

    # Record the neutral behavioral FACT for the resolved Person (design doc §7).
    # Separate from fraud scoring above: this is an observation, not a judgment,
    # and DELIVERED (a positive signal fraud does not track) is recorded too.
    # Resilient by contract — a facts-side failure must not roll back the
    # transition, so it is isolated in a savepoint and swallowed.
    if to_status in {OrderStatus.DELIVERED, OrderStatus.RTO_RETURNED, OrderStatus.CANCELLED}:
        try:
            with transaction.atomic():
                from identity.services import behavioral

                if to_status == OrderStatus.DELIVERED:
                    behavioral.record_order_delivered(order)
                elif to_status == OrderStatus.RTO_RETURNED:
                    behavioral.record_order_rto(order)
                else:
                    behavioral.record_order_cancelled(order)
        except Exception:  # noqa: BLE001
            logger.exception(
                "behavioral aggregate update failed for order %s -> %s",
                order.id,
                to_status,
            )
    return order


def update_order_status_from_courier(*, order_id: str, new_status: str, meta: dict) -> None:
    """The only way shipping is allowed to mutate an Order."""
    order = Order.objects.get(id=order_id, deleted_at__isnull=True)
    actor_user_id = meta.get("actor_user_id")
    reason = meta.get("reason") or f"Courier webhook status update: {new_status}"
    
    try:
        order_transition(
            order=order,
            to_status=new_status,
            actor_user_id=actor_user_id,
            actor_role="MANAGER",
            reason=reason,
        )
    except ValueError:
        order_transition(
            order=order,
            to_status=OrderStatus.ON_HOLD,
            actor_user_id=actor_user_id,
            actor_role="MANAGER",
            reason=f"Courier webhook inconsistent transition from {order.status} with event {new_status}",
        )
