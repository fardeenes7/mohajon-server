"""
Async identity-graph resolution.

Triggered explicitly (transaction.on_commit) from orders.services.checkout, NOT
via a post_save signal — the existing fraud/signals post_save pattern is the
anti-pattern this design deliberately avoids (it fires on every save and is
hard to reason about). One idempotent task per order.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def resolve_order_identity_graph(order_id: str) -> dict:
    """
    Resolve the raw signals captured in an OrderIdentitySnapshot into the
    global identity graph: get-or-create ContactPoints, back-fill the
    snapshot's resolved FKs, upsert CustomerContactPoint junctions, and create
    co-occurrence ContactPointLink edges.

    Idempotent at the order level via snapshot.resolved_at: a re-delivered or
    retried task for an already-resolved order is a no-op (so usage counters
    are not double-counted on the happy path).
    """
    from identity.models import (
        ContactPointLinkReason,
        ContactPointType,
        OrderIdentitySnapshot,
    )
    from identity.services import hashing, resolution

    try:
        snapshot = OrderIdentitySnapshot.objects.select_related("order").get(order_id=order_id)
    except OrderIdentitySnapshot.DoesNotExist:
        logger.warning("resolve_order_identity_graph: no snapshot for order %s", order_id)
        return {"status": "no_snapshot", "order_id": order_id}

    if snapshot.resolved_at is not None:
        return {"status": "already_resolved", "order_id": order_id}

    now = timezone.now()

    with transaction.atomic():
        resolved: dict[str, object] = {}

        # 1. Resolve each present signal → a global ContactPoint.
        if snapshot.phone_hash:
            resolved["phone_contact_point"] = resolution.get_or_create_contact_point(
                point_type=ContactPointType.PHONE,
                value_hash=snapshot.phone_hash,
                value_suffix=hashing.phone_suffix(snapshot.raw_phone),
                display_value=snapshot.display_phone,
            )

        if snapshot.address_hash:
            resolved["address_contact_point"] = resolution.get_or_create_contact_point(
                point_type=ContactPointType.ADDRESS,
                value_hash=snapshot.address_hash,
                display_value=snapshot.display_address[:255],
            )

        channel_type = {
            "FACEBOOK": ContactPointType.FACEBOOK,
            "WHATSAPP": ContactPointType.WHATSAPP,
        }.get(snapshot.channel)
        if channel_type and snapshot.channel_identity:
            resolved["channel_contact_point"] = resolution.get_or_create_contact_point(
                point_type=channel_type,
                value_hash=hashing.hash_channel_identity(snapshot.channel_identity),
                display_value="",  # PSID is opaque; nothing safe to display
            )

        # 2. Back-fill the snapshot's resolved FKs.
        for field, contact_point in resolved.items():
            setattr(snapshot, field, contact_point)

        # 3. CustomerContactPoint junctions (only when the order has a profile).
        customer_profile_id = snapshot.order.customer_profile_id
        if customer_profile_id:
            for contact_point in resolved.values():
                resolution.upsert_customer_contact_point(
                    customer_profile_id=customer_profile_id,
                    contact_point=contact_point,
                    when=now,
                )

        # 4. Co-occurrence edges between every unordered pair of resolved points.
        contact_points = list(resolved.values())
        for i in range(len(contact_points)):
            for j in range(i + 1, len(contact_points)):
                resolution.upsert_contact_point_link(
                    cp_a=contact_points[i],
                    cp_b=contact_points[j],
                    link_reason=ContactPointLinkReason.CO_OCCURRED_IN_ORDER,
                    when=now,
                )

        # 5. Layer 3 — attribute the order's signals to a Person (evidence tiers,
        #    design doc §6) and resolve/link the order's ChannelActor. The
        #    snapshot's resolved ContactPoint FKs are already set in-memory
        #    (step 2), so attribution reads them without a reload. Kept inside the
        #    same transaction and behind the resolved_at guard, so Person
        #    resolution is as idempotent as the rest of the task.
        person = resolution.attribute_snapshot_to_person(snapshot, when=now)

        # 6. Back-link the order's tenant-scoped CustomerProfile to the resolved
        #    global Person. Cross-app write goes through shops.services by UUID
        #    (never importing shops models here) to preserve the app seam. A
        #    shops-side failure must not roll back identity resolution, so it is
        #    isolated in its own savepoint and swallowed.
        if customer_profile_id and person is not None:
            try:
                with transaction.atomic():
                    from shops.services import link_profile_to_person

                    link_profile_to_person(
                        customer_profile_id=str(customer_profile_id),
                        person_id=str(person.id),
                    )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "link_profile_to_person failed for order %s (profile=%s person=%s)",
                    order_id,
                    customer_profile_id,
                    person.id,
                )

        # 7. Record the neutral "order placed" FACT for the resolved Person
        #    (design doc §7). Done here, not at checkout, because the Person is
        #    only attributed now (checkout runs before async resolution). Behind
        #    the resolved_at guard so a retried task never double-counts.
        #    Resilient: a facts-side failure must not roll back resolution.
        if person is not None:
            try:
                with transaction.atomic():
                    from identity.services import behavioral

                    behavioral.record_order_placed(order=snapshot.order, when=now)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "record_order_placed failed for order %s (person=%s)",
                    order_id,
                    person.id,
                )

        snapshot.resolved_at = now
        snapshot.save(
            update_fields=[
                "phone_contact_point",
                "address_contact_point",
                "channel_contact_point",
                "resolved_at",
                "updated_at",
            ]
        )

    return {
        "status": "resolved",
        "order_id": order_id,
        "contact_points": len(resolved),
        "person_id": str(person.id) if person is not None else None,
    }
