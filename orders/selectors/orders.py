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


def get_order_shipping_details(*, order_id: str) -> dict | None:
    """
    Read-only projection of the metadata a courier needs to book a shipment.

    Keeps the shipping app decoupled from Order internals — it consumes this
    normalized dict rather than importing Order objects.
    """
    order = (
        Order.objects.filter(id=order_id, deleted_at__isnull=True)
        .select_related('shipping_address', 'phone_identity')
        .first()
    )
    if not order:
        return None

    address = order.shipping_address
    recipient_name = address.contact_name if address else ''
    recipient_phone = (
        (address.contact_phone if address else '')
        or (order.phone_identity.phone_number if order.phone_identity_id else '')
    )
    recipient_address = ', '.join(
        part for part in [
            address.street if address else '',
            address.city if address else '',
            address.postal_code if address else '',
        ] if part
    )

    # COD orders collect the full total; prepaid collect nothing.
    amount_to_collect = (
        float(order.total_amount)
        if order.payment_method == Order.PAYMENT_METHOD_COD
        else 0.0
    )
    item_quantity = sum(item.quantity for item in order.items.all()) or 1

    return {
        'order_id': str(order.id),
        'shop_id': str(order.shop_id),
        'merchant_order_id': str(order.id),
        'recipient_name': recipient_name,
        'recipient_phone': recipient_phone,
        'recipient_address': recipient_address,
        'recipient_city': None,
        'recipient_zone': None,
        'recipient_area': None,
        'amount_to_collect': amount_to_collect,
        'item_quantity': item_quantity,
        'item_weight': 0.5,
        'special_instruction': '',
    }


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
