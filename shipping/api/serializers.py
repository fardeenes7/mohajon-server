from rest_framework import serializers
from shipping.models import CourierAccount, CourierConsignment, CourierProvider, CourierConsignmentStatus

class CourierAccountSerializer(serializers.ModelSerializer):
    provider_display = serializers.CharField(source='get_provider_display', read_only=True)
    credentials = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = CourierAccount
        fields = [
            'id', 'provider', 'provider_display', 'default_store_id', 
            'label', 'is_active', 'is_test_mode', 'credentials'
        ]
        read_only_fields = ['id', 'provider_display']

    def get_credentials(self, obj) -> dict:
        """
        Safely expose keys without value strings (or masked) for UI rendering context.
        We shouldn't send raw passwords/secrets back to the client.
        """
        from shipping.services import get_courier_credentials
        creds = get_courier_credentials(shop_id=str(obj.shop_id), provider=obj.provider)
        if not creds:
            return {}
        # Mask everything except keys
        return {key: "********" for key in creds.keys()}

class CourierConsignmentSerializer(serializers.ModelSerializer):
    provider_display = serializers.CharField(source='get_provider_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = CourierConsignment
        fields = [
            'id', 'order', 'shop', 'provider', 'provider_display',
            'external_consignment_id', 'tracking_code', 'status', 'status_display',
            'created_at', 'updated_at'
        ]
        read_only_fields = fields
