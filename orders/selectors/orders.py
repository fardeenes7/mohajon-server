from django.db.models import QuerySet

from orders.models import Order


def order_get_by_id(*, order_id: str, shop_id: str) -> Order:
    return Order.objects.get(id=order_id, shop_id=shop_id, deleted_at__isnull=True)


def order_list_for_shop(*, shop_id: str) -> QuerySet[Order]:
    return Order.objects.filter(shop_id=shop_id, deleted_at__isnull=True).order_by('-created_at')


def get_order_shop_id(*, order_id: str) -> str | None:
    return Order.objects.filter(id=order_id, deleted_at__isnull=True).values_list('shop_id', flat=True).first()


def get_order_terminal_person_id(order_id: str) -> str | None:
    order = Order.objects.filter(id=order_id, deleted_at__isnull=True).first()
    if not order:
        return None
    from identity.services.behavioral import _terminal_person_for_order
    person = _terminal_person_for_order(order)
    return str(person.id) if person else None


def order_list_for_customer(customer_profile_id: str, shop_id: str) -> QuerySet[Order]:
    """
    Returns orders belonging to the given person ID (customer_profile_id),
    resolved via the identity graph.
    """
    from django.db.models import Q
    from identity.models import OrderIdentitySnapshot

    order_ids = OrderIdentitySnapshot.objects.filter(
        order__shop_id=shop_id,
        order__deleted_at__isnull=True,
    ).filter(
        Q(phone_contact_point__person_id=customer_profile_id)
        | Q(address_contact_point__person_id=customer_profile_id)
        | Q(channel_contact_point__person_id=customer_profile_id)
    ).values_list('order_id', flat=True)

    return Order.objects.filter(id__in=order_ids).order_by("-created_at")
