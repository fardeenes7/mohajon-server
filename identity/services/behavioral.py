"""
Behavioral aggregate service — the neutral FACTS seam (design doc §7, Phase 4.1).

`orders` calls these on each lifecycle transition (order created / delivered /
RTO / cancelled) to record WHAT HAPPENED. Nothing here scores or judges — that
is `fraud`'s job, and it reads this aggregate through identity.services only.

Attribution is by the order's resolved Person: we look up the Person the
identity graph already attributed to the order's snapshot, following any merge
to the terminal node, so counts are cross-channel by construction. An order
with no resolved Person (unresolved snapshot, or no identity signal at all) is
skipped — the async resolver will have attributed a Person before delivery in
the normal flow, and a missing count is preferable to a wrong one.

All writers are idempotent-friendly F() increments inside the caller's
transaction, and are resilient by contract: callers wrap them so a facts-side
failure never rolls back the order transition.
"""

from __future__ import annotations

from django.db.models import F
from django.utils import timezone

from identity.models import Person, PersonBehavioralAggregate


def _terminal_person_for_order(order) -> Person | None:
    """
    Resolve the terminal Person attributed to this order via its snapshot.

    Reads OrderIdentitySnapshot rather than re-resolving raw signals: the async
    resolver owns attribution and has already run by delivery time. Returns None
    when nothing has been attributed yet (nothing safe to count).
    """
    from identity.services.resolution import resolve_terminal_person

    snapshot = getattr(order, "identity_snapshot", None)
    if snapshot is None:
        return None

    # The snapshot itself does not store the Person; the Person owns the
    # snapshot's resolved ContactPoints. Prefer the phone anchor, then address,
    # then channel — the same precedence the resolver uses.
    for cp in (
        snapshot.phone_contact_point,
        snapshot.address_contact_point,
        snapshot.channel_contact_point,
    ):
        if cp is not None and cp.person_id is not None:
            return resolve_terminal_person(cp.person)
    return None


def _aggregate_for_order(order):
    """Get-or-create the aggregate row for the order's terminal Person, or None
    when the order has no resolved Person to attribute to."""
    person = _terminal_person_for_order(order)
    if person is None:
        return None
    aggregate, _ = PersonBehavioralAggregate.objects.get_or_create(person=person)
    return aggregate


def record_order_placed(order, *, when=None) -> bool:
    """Increment orders_placed and stamp first/last order timestamps."""
    when = when or timezone.now()
    aggregate = _aggregate_for_order(order)
    if aggregate is None:
        return False
    updates = {
        "orders_placed": F("orders_placed") + 1,
        "last_order_at": when,
        "updated_at": when,
    }
    if aggregate.first_order_at is None:
        updates["first_order_at"] = when
    PersonBehavioralAggregate.objects.filter(pk=aggregate.pk).update(**updates)
    return True


def _record_outcome(order, field: str, *, when=None) -> bool:
    when = when or timezone.now()
    aggregate = _aggregate_for_order(order)
    if aggregate is None:
        return False
    PersonBehavioralAggregate.objects.filter(pk=aggregate.pk).update(
        **{field: F(field) + 1, "updated_at": when}
    )
    return True


def record_order_delivered(order, *, when=None) -> bool:
    """Increment orders_delivered (a positive-trust signal)."""
    return _record_outcome(order, "orders_delivered", when=when)


def record_order_rto(order, *, when=None) -> bool:
    """Increment orders_rto (return-to-origin — the key COD risk fact)."""
    return _record_outcome(order, "orders_rto", when=when)


def record_order_cancelled(order, *, when=None) -> bool:
    """Increment orders_cancelled."""
    return _record_outcome(order, "orders_cancelled", when=when)


def ingest_courier_success_rate(*, person_id: str, rate: float, when=None) -> bool:
    """
    Store a courier-reported delivery-success rate (0.0–1.0) for a Person.

    An ingested observation, not a computed judgment. Clamped to [0, 1] so a bad
    feed value cannot poison downstream reads. Returns False for an unknown
    person or out-of-range rate that cannot be clamped meaningfully.
    """
    when = when or timezone.now()
    if rate is None:
        return False
    clamped = max(0.0, min(1.0, float(rate)))
    aggregate, _ = PersonBehavioralAggregate.objects.get_or_create(person_id=person_id)
    PersonBehavioralAggregate.objects.filter(pk=aggregate.pk).update(
        courier_success_rate=clamped,
        courier_rate_updated_at=when,
        updated_at=when,
    )
    return True


def merge_aggregates(*, winner_person_id: str, loser_person_id: str, when=None) -> None:
    """
    Fold the loser Person's aggregate into the winner's on a Person merge.

    Called by resolution.merge_persons so counts survive a merge instead of
    being orphaned on the tombstoned loser. Sums the counts, keeps the earliest
    first_order_at / latest last_order_at, and prefers the most recently updated
    courier rate. The loser's aggregate is deleted after folding.
    """
    when = when or timezone.now()
    try:
        loser = PersonBehavioralAggregate.objects.get(person_id=loser_person_id)
    except PersonBehavioralAggregate.DoesNotExist:
        return

    winner, _ = PersonBehavioralAggregate.objects.get_or_create(person_id=winner_person_id)

    winner.orders_placed += loser.orders_placed
    winner.orders_delivered += loser.orders_delivered
    winner.orders_rto += loser.orders_rto
    winner.orders_cancelled += loser.orders_cancelled

    # Earliest first, latest last.
    if loser.first_order_at and (
        winner.first_order_at is None or loser.first_order_at < winner.first_order_at
    ):
        winner.first_order_at = loser.first_order_at
    if loser.last_order_at and (
        winner.last_order_at is None or loser.last_order_at > winner.last_order_at
    ):
        winner.last_order_at = loser.last_order_at

    # Prefer the freshest courier rate.
    if loser.courier_rate_updated_at and (
        winner.courier_rate_updated_at is None
        or loser.courier_rate_updated_at > winner.courier_rate_updated_at
    ):
        winner.courier_success_rate = loser.courier_success_rate
        winner.courier_rate_updated_at = loser.courier_rate_updated_at

    winner.updated_at = when
    winner.save()
    loser.delete()


def get_facts_by_phone(*, phone_number: str) -> dict | None:
    """
    Facts for the Person who owns a phone number, resolved by canonical hash so
    any input format finds the same PHONE ContactPoint → Person → aggregate.

    This is the seam `fraud` uses to fold behavioral facts into a risk score
    WITHOUT importing identity models. Returns None when the phone maps to no
    resolved Person yet (no orders attributed), so the caller can distinguish
    "no history" from "clean history".
    """
    from core.phone import hash_phone

    from identity.models import ContactPoint, ContactPointType
    from identity.services.resolution import resolve_terminal_person

    value_hash = hash_phone(phone_number)
    if not value_hash:
        return None

    cp = (
        ContactPoint.objects.filter(
            point_type=ContactPointType.PHONE, value_hash=value_hash
        )
        .only("id", "person_id")
        .first()
    )
    if cp is None or cp.person_id is None:
        return None

    terminal = resolve_terminal_person(cp.person)
    return get_person_facts(person_id=str(terminal.id))


def get_person_facts(*, person_id: str) -> dict:
    """
    Read-only facts for a Person, for `fraud` to score over. Returns zeros for a
    Person with no aggregate yet so callers get a stable shape. This is the ONLY
    way fraud should read behavioral data — never by importing the model.
    """
    try:
        agg = PersonBehavioralAggregate.objects.get(person_id=person_id)
    except PersonBehavioralAggregate.DoesNotExist:
        return {
            "orders_placed": 0,
            "orders_delivered": 0,
            "orders_rto": 0,
            "orders_cancelled": 0,
            "courier_success_rate": None,
        }
    return {
        "orders_placed": agg.orders_placed,
        "orders_delivered": agg.orders_delivered,
        "orders_rto": agg.orders_rto,
        "orders_cancelled": agg.orders_cancelled,
        "courier_success_rate": agg.courier_success_rate,
    }
