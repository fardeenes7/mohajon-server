"""
Phase 1 unify-phone-hashing (identity app).

Adds a `hash_version` stamp to ContactPoint and OrderIdentitySnapshot, then
rehashes every PHONE hash off the shared canonical `core.phone` (E.164) so that
identity PHONE hashes join EXACTLY with users.PhoneIdentity.phone_hash and
fraud.GlobalFraudPool.phone_hash. The old national-core divergence is gone.

Source of raw phone: OrderIdentitySnapshot.raw_phone (ContactPoint does NOT
store a raw phone — only value_hash / value_suffix / masked display_value), so
PHONE ContactPoint hashes are rebuilt from the snapshots that reference them.

Historical raw_phone parsing: pre-unify snapshots stored the OLD national-core
form (digits only, country code + trunk-0 stripped, e.g. '1712345678'). Verified
that core.phone.normalize_phone_e164('1712345678', region=BD) parses cleanly to
'+8801712345678' (phonenumbers treats the bare national significant number as a
BD number), so NO leading '0'/'+880' prepend is required. Post-unify snapshots
already store full E.164 ('+8801712345678'), which also round-trips unchanged.
Empty / unparseable values hash to "" and are skipped, never crashing.
"""

from __future__ import annotations

from collections import defaultdict

from django.db import migrations, models


def rehash_phone_hashes(apps, schema_editor):
    from core.phone import HASH_VERSION, hash_phone

    ContactPoint = apps.get_model("identity", "ContactPoint")
    CustomerContactPoint = apps.get_model("identity", "CustomerContactPoint")
    ContactPointLink = apps.get_model("identity", "ContactPointLink")
    OrderIdentitySnapshot = apps.get_model("identity", "OrderIdentitySnapshot")

    # ── Helpers ──────────────────────────────────────────────────────────────
    def merge_contact_point(dup, survivor):
        """Repoint every reference from `dup` onto `survivor`, then delete
        `dup`. Respects the junction/link unique + canonical-order constraints
        by dropping redundant rows rather than creating duplicates."""
        # CustomerContactPoint: unique (customer_profile, contact_point).
        for ccp in CustomerContactPoint.objects.filter(contact_point=dup):
            clash = CustomerContactPoint.objects.filter(
                customer_profile_id=ccp.customer_profile_id, contact_point=survivor
            ).exists()
            if clash:
                ccp.delete()
            else:
                ccp.contact_point = survivor
                ccp.save(update_fields=["contact_point"])

        # Snapshot resolved FKs (no unique constraints — plain repoint).
        for field in ("phone_contact_point", "address_contact_point", "channel_contact_point"):
            OrderIdentitySnapshot.objects.filter(**{field: dup}).update(**{field: survivor})

        # ContactPointLink: keep source_id < target_id and (source,target,reason)
        # unique. A dup endpoint may collapse into survivor.
        links = ContactPointLink.objects.filter(
            models.Q(source=dup) | models.Q(target=dup)
        )
        for link in list(links):
            s = survivor.id if link.source_id == dup.id else link.source_id
            t = survivor.id if link.target_id == dup.id else link.target_id
            if s == t:
                # Would become a self-loop — violates the canonical-order CHECK.
                link.delete()
                continue
            if s > t:
                s, t = t, s
            redundant = (
                ContactPointLink.objects.filter(
                    source_id=s, target_id=t, link_reason=link.link_reason
                )
                .exclude(id=link.id)
                .exists()
            )
            if redundant:
                link.delete()
                continue
            link.source_id = s
            link.target_id = t
            link.save(update_fields=["source", "target"])

        dup.delete()

    # ── 1. Rehash every OrderIdentitySnapshot.phone_hash from its raw_phone ───
    for snap in OrderIdentitySnapshot.objects.exclude(raw_phone="").iterator():
        new_hash = hash_phone(snap.raw_phone)
        if not new_hash:
            continue  # unparseable / empty — leave legacy row untouched
        snap.phone_hash = new_hash
        snap.hash_version = HASH_VERSION
        snap.save(update_fields=["phone_hash", "hash_version"])

    # ── 2. Rebuild PHONE ContactPoint hashes from their backing snapshots ─────
    # Only ContactPoints reachable via snapshot.phone_contact_point have a
    # recoverable raw phone; PHONE ContactPoints with no snapshot backref keep
    # their legacy value_hash and hash_version 0.
    cp_new_hash: dict = {}
    snaps = OrderIdentitySnapshot.objects.exclude(raw_phone="").exclude(
        phone_contact_point__isnull=True
    )
    for snap in snaps.iterator():
        new_hash = hash_phone(snap.raw_phone)
        if new_hash:
            cp_new_hash[snap.phone_contact_point_id] = new_hash

    # Group ContactPoints by the hash they should collapse to; multiple distinct
    # legacy PHONE ContactPoints can reduce to one canonical hash → MERGE.
    groups: dict = defaultdict(list)
    for cp_id, new_hash in cp_new_hash.items():
        groups[new_hash].append(cp_id)

    for new_hash, cp_ids in groups.items():
        members = list(
            ContactPoint.objects.filter(id__in=cp_ids, point_type="PHONE")
        )
        # A pre-existing PHONE ContactPoint that already carries this exact hash
        # (e.g. a partially-migrated row) must be folded in too so we never trip
        # the (point_type, value_hash) unique constraint.
        existing = list(
            ContactPoint.objects.filter(point_type="PHONE", value_hash=new_hash)
            .exclude(id__in=cp_ids)
        )
        candidates = members + existing
        if not candidates:
            continue

        survivor = min(candidates, key=lambda cp: cp.created_at)
        dupes = [cp for cp in candidates if cp.id != survivor.id]

        # Delete/repoint dupes FIRST so assigning the new hash to the survivor
        # cannot transiently collide on the unique constraint.
        for dup in dupes:
            merge_contact_point(dup, survivor)

        if survivor.value_hash != new_hash or survivor.hash_version != HASH_VERSION:
            survivor.value_hash = new_hash
            survivor.hash_version = HASH_VERSION
            survivor.save(update_fields=["value_hash", "hash_version"])


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="contactpoint",
            name="hash_version",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="orderidentitysnapshot",
            name="hash_version",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.RunPython(rehash_phone_hashes, migrations.RunPython.noop),
    ]
