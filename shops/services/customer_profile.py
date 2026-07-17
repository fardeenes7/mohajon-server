"""
CustomerProfile services — the shops-side seam for the identity graph.

A CustomerProfile is the tenant-scoped PROJECTION of a global identity.Person.
identity resolution owns the Person; when it attributes an order to a Person it
calls back here (by UUID, never importing identity models) to point the order's
profile at that Person. Keeping the write on the shops side preserves the
app boundary so the two can split into separate services later.
"""

from __future__ import annotations

from shops.models import CustomerProfile


def link_profile_to_person(*, customer_profile_id: str, person_id: str) -> bool:
    """
    Point a CustomerProfile at its resolved global Person.

    Idempotent and by-UUID (no identity import): a no-op when the profile is
    gone or already linked to this person. Returns True when a write happened,
    False otherwise, for caller visibility. Uses a scoped UPDATE so it neither
    clobbers a concurrently-set link with a stale in-memory row nor touches
    other fields.
    """
    if not customer_profile_id or not person_id:
        return False

    updated = (
        CustomerProfile.objects.filter(id=customer_profile_id)
        .exclude(person_id=person_id)
        .update(person_id=person_id)
    )
    return bool(updated)
