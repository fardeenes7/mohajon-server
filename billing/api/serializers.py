from rest_framework import serializers
from billing.models import (
    ShopSubscription, PaymentGatewayConfig, PaymentMethod, 
    MerchantAPIToken, OutboundWebhook, AICreditPackage, AICreditTopUp
)

class AICreditPackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AICreditPackage
        fields = ["id", "name", "credits", "retail_price_bdt"]

class AICreditTopUpSerializer(serializers.ModelSerializer):
    class Meta:
        model = AICreditTopUp
        fields = [
            "id", "credits_purchased", "amount_paid_bdt", "status", 
            "expires_at", "is_expired", "created_at"
        ]
        read_only_fields = fields

class ShopSubscriptionSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    tier_display = serializers.CharField(source='get_tier_display', read_only=True)
    suspension_banner = serializers.CharField(read_only=True)
    
    class Meta:
        model = ShopSubscription
        fields = [
            'id', 'tier', 'tier_display', 'status', 'status_display',
            'current_period_start', 'current_period_end', 'grace_period_until',
            'last_paid_at', 'is_billing_exempt', 'suspension_banner'
        ]
        read_only_fields = fields

class PaymentGatewayConfigSerializer(serializers.ModelSerializer):
    gateway_display = serializers.CharField(source='get_gateway_display', read_only=True)
    
    class Meta:
        model = PaymentGatewayConfig
        fields = ['id', 'gateway', 'gateway_display', 'label', 'is_active', 'is_test_mode']
        read_only_fields = ['id', 'gateway_display']

class PaymentMethodSerializer(serializers.ModelSerializer):
    method_display = serializers.CharField(source='get_method_display', read_only=True)

    def validate_method(self, value):
        if value not in PaymentMethod.ALLOWED_METHODS:
            raise serializers.ValidationError("Unsupported payment method.")
        return value
    
    class Meta:
        model = PaymentMethod
        fields = [
            'id', 'method', 'method_display', 'is_enabled', 
            'display_order', 'fee_payer', 'custom_instructions', 
            'requires_transaction_id'
        ]
        read_only_fields = ['id', 'method_display']

class MerchantAPITokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = MerchantAPIToken
        fields = ['id', 'name', 'token_prefix', 'scopes', 'expires_at', 'last_used_at', 'created_at']
        read_only_fields = ['id', 'token_prefix', 'last_used_at', 'created_at']

class OutboundWebhookSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = OutboundWebhook
        fields = [
            'id', 'url', 'status', 'status_display', 'subscribed_events', 
            'last_triggered_at', 'last_success_at'
        ]
        read_only_fields = ['id', 'status_display', 'last_triggered_at', 'last_success_at']
