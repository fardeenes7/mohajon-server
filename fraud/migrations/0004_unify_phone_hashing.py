# Repoint the fraud app onto core.phone (E.164 canonical hashing).
#
# Adds hash_version to FraudReport and GlobalFraudPool, rehashes every
# FraudReport under the new E.164 hash space, and REBUILDS GlobalFraudPool
# from FraudReport so that numbers that hashed separately under the old
# raw-digits scheme (e.g. '01712...' vs '8801712...') merge into one row.

from django.db import migrations, models

from core.phone import HASH_VERSION, hash_phone


def rehash_and_rebuild_pool(apps, schema_editor):
    FraudReport = apps.get_model("fraud", "FraudReport")
    GlobalFraudPool = apps.get_model("fraud", "GlobalFraudPool")
    FraudConfig = apps.get_model("fraud", "FraudConfig")

    reason_field = {
        "RTO": "rto_count",
        "FAKE_ORDER": "fake_order_count",
        "HARASSMENT": "harassment_count",
        "UNPAID": "unpaid_count",
    }

    # ASSUMPTION: historical per-report opt-in state is not recoverable, so we
    # use the shop's CURRENT FraudConfig.opt_in_pooling as the best available
    # signal for whether a report should contribute to the global pool. A shop
    # with no FraudConfig defaults to opted-in (matches the model default).
    opt_in_by_shop = dict(
        FraudConfig.objects.values_list("shop_id", "opt_in_pooling")
    )

    # Re-aggregate pool counts per NEW hash. This naturally merges old rows
    # that used to hash separately (raw digits) into a single E.164 hash.
    pool_counts = {}

    for report in FraudReport.objects.all().iterator():
        new_hash = hash_phone(report.phone_number)
        # Defensive: skip unparseable/empty numbers so we never crash or write
        # a bogus empty-string hash.
        if not new_hash:
            continue

        # Rehash the report itself (phone_hash is non-unique / indexed, so no
        # merge concern here).
        FraudReport.objects.filter(pk=report.pk).update(
            phone_hash=new_hash,
            hash_version=HASH_VERSION,
        )

        # Only pooled shops contribute to the rebuilt global pool.
        if not opt_in_by_shop.get(report.shop_id, True):
            continue

        field = reason_field.get(report.reason)
        if field is None:
            continue

        bucket = pool_counts.setdefault(
            new_hash,
            {
                "rto_count": 0,
                "fake_order_count": 0,
                "harassment_count": 0,
                "unpaid_count": 0,
            },
        )
        bucket[field] += 1

    # Clean rebuild: drop every old pool row and recreate from the
    # re-aggregation under the new hash space.
    GlobalFraudPool.objects.all().delete()

    GlobalFraudPool.objects.bulk_create(
        [
            GlobalFraudPool(
                phone_hash=phone_hash,
                hash_version=HASH_VERSION,
                rto_count=counts["rto_count"],
                fake_order_count=counts["fake_order_count"],
                harassment_count=counts["harassment_count"],
                unpaid_count=counts["unpaid_count"],
            )
            for phone_hash, counts in pool_counts.items()
        ]
    )


class Migration(migrations.Migration):

    dependencies = [
        ("fraud", "0003_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="fraudreport",
            name="hash_version",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="globalfraudpool",
            name="hash_version",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.RunPython(
            rehash_and_rebuild_pool,
            migrations.RunPython.noop,
        ),
    ]
