"""
Address preload — the Phase 5 UX/trust payoff (design doc §8).

Given a conversation endpoint (channel + channel_identity) or a phone, surface
the addresses a returning customer has SUCCESSFULLY received orders at before, so
chat/checkout can pre-fill them. A returning WhatsApp buyer sees their known
address without retyping it — a UX touch and a trust signal.

"Successful" is deliberately strict: only addresses from DELIVERED orders are
offered, so we never re-suggest an address that bounced (RTO). Addresses are
read across ALL the Person's channels (the whole point of Layer 3): an address
first used on the website is offered on WhatsApp.

Reads live entirely inside identity (Person → snapshots → orders). Callers in
other apps reach this through identity.services, never by touching the models.
"""

from __future__ import annotations

from django.db.models import Q

from identity.models import (
    ChannelActor,
    OrderIdentitySnapshot,
    Person,
)
from identity.services.resolution import resolve_terminal_person

# Only these order states count an address as "successfully delivered to".
_DELIVERED_STATUSES = ("DELIVERED",)
# Cap suggestions so the bot presents a short, useful list, newest first.
_DEFAULT_LIMIT = 5


def _person_for_channel(*, shop_id: str, channel: str, channel_identity: str) -> Person | None:
    """Resolve the terminal Person behind a conversation endpoint, or None."""
    actor = (
        ChannelActor.objects.filter(
            shop_id=shop_id, channel=channel, channel_identity=channel_identity
        )
        .only("id", "person_id")
        .first()
    )
    if actor is None or actor.person_id is None:
        return None
    return resolve_terminal_person(actor.person)


def _successful_addresses_for_person(person: Person, *, limit: int) -> list[dict]:
    """
    Distinct display addresses from the Person's DELIVERED orders, newest first.

    Joins snapshots to the Person through ANY resolved ContactPoint the Person
    owns (phone / address / channel), so an address is surfaced regardless of
    which signal tied the order to them. Dedupes on address_hash so the same
    physical address used many times appears once.
    """
    snapshots = (
        OrderIdentitySnapshot.objects.filter(
            order__status__in=_DELIVERED_STATUSES,
            address_hash__gt="",
        )
        .filter(
            # The order is attributed to this Person via ANY of its resolved
            # signals — phone, address, or channel. An address first used on the
            # web is thus surfaced on WhatsApp (the cross-channel payoff).
            Q(phone_contact_point__person=person)
            | Q(address_contact_point__person=person)
            | Q(channel_contact_point__person=person)
        )
        .select_related("order")
        .order_by("-created_at")
    )

    seen_hashes: set[str] = set()
    addresses: list[dict] = []
    for snap in snapshots.iterator():
        if snap.address_hash in seen_hashes:
            continue
        seen_hashes.add(snap.address_hash)
        addresses.append(
            {
                "display_address": snap.display_address,
                "display_name": snap.display_name,
                "display_phone": snap.display_phone,
                "address_hash": snap.address_hash,
                "last_used_at": snap.created_at.isoformat() if snap.created_at else None,
            }
        )
        if len(addresses) >= limit:
            break
    return addresses


def preload_addresses_for_channel(
    *, shop_id: str, channel: str, channel_identity: str, limit: int = _DEFAULT_LIMIT
) -> list[dict]:
    """
    Successful delivery addresses for the customer behind a conversation endpoint.

    Returns [] (never raises) when the endpoint maps to no resolved Person yet or
    has no delivered history — a first-time buyer simply gets no suggestions.
    This is the seam chat calls to pre-fill an address.
    """
    person = _person_for_channel(
        shop_id=shop_id, channel=channel, channel_identity=channel_identity
    )
    if person is None:
        return []
    return _successful_addresses_for_person(person, limit=limit)


def preload_addresses_for_phone(*, phone_number: str, limit: int = _DEFAULT_LIMIT) -> list[dict]:
    """
    Successful delivery addresses for the Person who owns a phone number, resolved
    by canonical hash. The web/checkout entry point (no channel actor). Returns []
    when the phone maps to no resolved Person or has no delivered history.
    """
    from core.phone import hash_phone

    from identity.models import ContactPoint, ContactPointType

    value_hash = hash_phone(phone_number)
    if not value_hash:
        return []
    cp = (
        ContactPoint.objects.filter(
            point_type=ContactPointType.PHONE, value_hash=value_hash
        )
        .only("id", "person_id")
        .first()
    )
    if cp is None or cp.person_id is None:
        return []
    person = resolve_terminal_person(cp.person)
    return _successful_addresses_for_person(person, limit=limit)
