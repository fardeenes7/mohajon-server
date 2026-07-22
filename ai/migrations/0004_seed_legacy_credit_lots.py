from decimal import Decimal

from django.db import migrations


def seed_legacy_balances(apps, schema_editor):
    """
    Migrate existing flat ShopSettings.ai_credit_balance values into the new
    lot-based ledger so no shop loses credits at cutover.

    Each shop with a positive balance gets one non-expiring ADJUSTMENT lot
    whose granted/remaining equals the legacy balance. Idempotent: skips shops
    that already have any lot (safe to re-run).
    """
    ShopSettings = apps.get_model("shops", "ShopSettings")
    AICreditLot = apps.get_model("ai", "AICreditLot")

    for settings_obj in ShopSettings.objects.filter(
        deleted_at__isnull=True, ai_credit_balance__gt=0
    ).iterator():
        shop_id = settings_obj.shop_id
        if AICreditLot.objects.filter(shop_id=shop_id).exists():
            continue
        balance = Decimal(settings_obj.ai_credit_balance)
        AICreditLot.objects.create(
            shop_id=shop_id,
            tenant_id=shop_id,
            category="ADJUSTMENT",
            credits_granted=balance,
            credits_remaining=balance,
            expires_at=None,
            note="Migrated from legacy flat balance",
        )


def noop_reverse(apps, schema_editor):
    """Reverse is a deliberate no-op: keep migrated lots intact on rollback."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("ai", "0003_ai_credit_lot"),
        ("shops", "0003_customerprofile_person_customerprofile_shop_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_legacy_balances, noop_reverse),
    ]
