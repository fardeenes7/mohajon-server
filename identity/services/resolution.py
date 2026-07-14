"""
Pure, unit-testable resolution helpers for the identity graph.

Every write goes through get_or_create / update_or_create keyed by stable
identifiers (hashes, order ids, canonical edge ordering) so the async
orchestrator can be retried or re-run without producing duplicates or
double-counting.
"""

from __future__ import annotations

from django.db.models import F
from django.utils import timezone

from identity.models import (
    ContactPoint,
    ContactPointLink,
    ContactPointLinkReason,
    CustomerContactPoint,
)


def get_or_create_contact_point(
    *,
    point_type: str,
    value_hash: str,
    value_suffix: str | None = None,
    display_value: str = "",
) -> ContactPoint:
    """Dedup a signal to a single global node by (point_type, value_hash)."""
    contact_point, _created = ContactPoint.objects.get_or_create(
        point_type=point_type,
        value_hash=value_hash,
        defaults={
            "value_suffix": value_suffix,
            "display_value": display_value,
        },
    )
    return contact_point


def upsert_customer_contact_point(
    *,
    customer_profile_id: str,
    contact_point: ContactPoint,
    when=None,
) -> CustomerContactPoint:
    """
    Attach a ContactPoint to a shop's CustomerProfile, bumping usage counters.
    Idempotent per (customer_profile, contact_point): re-running increments
    use_count and advances last_used_at without creating a second row.
    """
    when = when or timezone.now()
    link, created = CustomerContactPoint.objects.get_or_create(
        customer_profile_id=customer_profile_id,
        contact_point=contact_point,
        defaults={
            "use_count": 1,
            "first_used_at": when,
            "last_used_at": when,
        },
    )
    if not created:
        CustomerContactPoint.objects.filter(pk=link.pk).update(
            use_count=F("use_count") + 1,
            last_used_at=when,
            updated_at=when,
        )
        link.refresh_from_db()
    return link


def _confidence_for_occurrences(occurrence_count: int) -> float:
    """
    Monotonic, bounded confidence from co-occurrence count. One shared order is
    weak (0.5); each additional co-occurrence adds 0.1 up to a 1.0 ceiling.
    Kept intentionally simple for v1 — a decay/reweighting sweep is deferred.
    """
    return min(1.0, 0.5 + 0.1 * max(0, occurrence_count - 1))


def upsert_contact_point_link(
    *,
    cp_a: ContactPoint,
    cp_b: ContactPoint,
    link_reason: str = ContactPointLinkReason.CO_OCCURRED_IN_ORDER,
    when=None,
) -> ContactPointLink | None:
    """
    Create-or-strengthen an UNDIRECTED edge between two ContactPoints.

    The pair is canonically ordered (smaller UUID as source) before writing, so
    A↔B and B↔A collapse to one row (matching the DB check + unique
    constraints). Returns None for a self-link (cp_a is cp_b).
    """
    if cp_a.id == cp_b.id:
        return None

    when = when or timezone.now()
    # Canonical ordering: source_id < target_id.
    source, target = (cp_a, cp_b) if cp_a.id < cp_b.id else (cp_b, cp_a)

    link, created = ContactPointLink.objects.get_or_create(
        source=source,
        target=target,
        link_reason=link_reason,
        defaults={
            "occurrence_count": 1,
            "confidence_score": _confidence_for_occurrences(1),
            "first_seen_at": when,
            "last_seen_at": when,
        },
    )
    if not created:
        new_count = link.occurrence_count + 1
        ContactPointLink.objects.filter(pk=link.pk).update(
            occurrence_count=new_count,
            confidence_score=_confidence_for_occurrences(new_count),
            last_seen_at=when,
            updated_at=when,
        )
        link.refresh_from_db()
    return link
