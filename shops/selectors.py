from __future__ import annotations

from shops.models import Shop, ShopSettings

def get_shop(shop_id: str) -> Shop | None:
    try:
        return Shop.objects.get(id=shop_id, deleted_at__isnull=True)
    except Shop.DoesNotExist:
        return None

def get_shop_settings(shop_id: str) -> ShopSettings | None:
    try:
        return ShopSettings.objects.get(shop_id=shop_id, deleted_at__isnull=True)
    except ShopSettings.DoesNotExist:
        return None
