from django.db import migrations, models

from core import phone as canonical_phone


def rehash_phone_identities(apps, schema_editor):
    """
    Re-normalize every PhoneIdentity.phone_number to E.164 and re-hash it under
    the canonical scheme in core.phone.

    Re-normalization can collapse several legacy rows onto one E.164 value
    (e.g. '01712345678' and '8801712345678' both become '+8801712345678'),
    which would violate the unique phone_number / phone_hash constraints. We
    therefore group rows by their new E.164 value, keep the OLDEST row as the
    survivor, repoint every FK off the duplicates onto the survivor, delete the
    duplicates, and only THEN write the new unique values.
    """
    PhoneIdentity = apps.get_model("users", "PhoneIdentity")
    Order = apps.get_model("orders", "Order")
    FraudProfile = apps.get_model("fraud", "FraudProfile")
    FraudEvent = apps.get_model("fraud", "FraudEvent")
    FraudReport = apps.get_model("fraud", "FraudReport")

    # Group rows by their new canonical E.164 value. Rows that cannot be parsed
    # (canonical == "") are skipped entirely: phone_number/phone_hash are left
    # untouched and hash_version is NOT stamped, since no hash was computed.
    groups = {}
    for row in PhoneIdentity.objects.all():
        canonical = canonical_phone.normalize_phone_e164(row.phone_number)
        if not canonical:
            continue
        groups.setdefault(canonical, []).append(row)

    for canonical, rows in groups.items():
        # Oldest first. created_at is auto_now_add so it should be set, but be
        # defensive about NULLs so a bad row can't crash the whole migration.
        rows_sorted = sorted(
            rows,
            key=lambda r: (r.created_at is None, r.created_at),
        )
        survivor = rows_sorted[0]
        duplicates = rows_sorted[1:]

        if duplicates:
            # Carry verification/trust forward: if any row in the group is
            # verified but the survivor is not, promote the survivor and take
            # the max trust_score across the group.
            any_verified = any(r.is_verified for r in rows)
            if any_verified and not survivor.is_verified:
                survivor.is_verified = True
                survivor.trust_score = max(r.trust_score for r in rows)

            for dup in duplicates:
                # Repoint FKs BEFORE deleting. orders.Order.phone_identity is
                # on_delete=PROTECT, so it must be moved off the duplicate first.
                Order.objects.filter(phone_identity_id=dup.id).update(
                    phone_identity_id=survivor.id
                )
                FraudProfile.objects.filter(phone_identity_id=dup.id).update(
                    phone_identity_id=survivor.id
                )
                FraudEvent.objects.filter(phone_identity_id=dup.id).update(
                    phone_identity_id=survivor.id
                )
                FraudReport.objects.filter(phone_identity_id=dup.id).update(
                    phone_identity_id=survivor.id
                )
                dup.delete()

        # Now the E.164 value is unique among live rows: write the canonical
        # phone_number and re-hash under the current scheme.
        survivor.phone_number = canonical
        survivor.phone_hash = canonical_phone.hash_phone(canonical)
        survivor.phone_suffix = canonical_phone.phone_suffix(canonical)
        survivor.hash_version = canonical_phone.HASH_VERSION
        survivor.save()


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0001_initial"),
        # Needed so the FK columns we repoint already exist in the schema.
        ("orders", "0003_initial"),
        ("fraud", "0003_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="phoneidentity",
            name="hash_version",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.RunPython(
            rehash_phone_identities,
            migrations.RunPython.noop,
        ),
    ]
