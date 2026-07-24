from rest_framework import serializers
from shops.models import Shop, ShopMember, ShopSettings, StoreTheme
from catalog.api.serializers import ShopTrackingConfigSerializer


class ShopSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shop
        fields = [
            "id", "name", "subdomain", "base_currency",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "subdomain", "created_at", "updated_at"]


class ActiveShopContextSerializer(serializers.Serializer):
    shop = ShopSerializer()
    role = serializers.ChoiceField(choices=ShopMember.ROLE_CHOICES)
    subscription = serializers.JSONField()


class ShopSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopSettings
        fields = [
            "id", "stock_reservation_minutes", "allow_guest_checkout",
            "mandatory_advance_fee_bdt", "maintenance_mode",
            "show_stock_count", "enable_product_reviews",
            "tax_calculation_base", "discount_application",
            "messenger_greeting_keywords", "notification_targets",
            "ai_credit_balance",
        ]
        read_only_fields = ["id", "ai_credit_balance"]

class StoreThemeSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreTheme
        fields = [
            "id", "theme_id", "aesthetic_overrides", 
            "active_components", "typography"
        ]
        read_only_fields = ["id"]

