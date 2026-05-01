"""
billing/services/gateway.py

Business logic for managing payment gateway credentials and method preferences.
Supports EPIC B: Payment Gateways & Processing.
"""

import json
from django.core.cache import cache
from shops.models import Shop
from billing.models import PaymentGatewayConfig, PaymentMethod

GATEWAY_CREDENTIALS_CACHE_TTL = 1800  # 30 minutes in seconds

def set_gateway_credentials(shop: Shop, gateway: str, credentials: dict, is_test_mode: bool = False):
    """
    Safely stores gateway credentials. 
    Implements the 30-minute preservation rule for active checkouts.
    """
    config, created = PaymentGatewayConfig.objects.get_or_create(
        shop=shop,
        gateway=gateway,
        defaults={'tenant_id': shop.id}
    )
    
    # Preservation Rule: Save old keys to cache for 30 minutes if this is an update
    if not created and config.credentials_encrypted:
        cache_key = f"gateway_cred_legacy:{shop.id}:{gateway}"
        cache.set(cache_key, config.credentials_encrypted, timeout=GATEWAY_CREDENTIALS_CACHE_TTL)
        
    config.credentials_encrypted = json.dumps(credentials)
    config.is_test_mode = is_test_mode
    config.save()
    return config

def get_active_gateway_credentials(shop: Shop, gateway: str, use_legacy_if_available: bool = False):
    """
    Retrieves credentials. If use_legacy_if_available is True, checks the 30-min cache first.
    """
    if use_legacy_if_available:
        cache_key = f"gateway_cred_legacy:{shop.id}:{gateway}"
        legacy_creds = cache.get(cache_key)
        if legacy_creds:
            return json.loads(legacy_creds)
            
    try:
        config = PaymentGatewayConfig.objects.get(shop=shop, gateway=gateway, is_active=True)
        return json.loads(config.credentials_encrypted)
    except PaymentGatewayConfig.DoesNotExist:
        return None

def toggle_payment_method(shop: Shop, method: str, is_enabled: bool):
    """
    Enables or disables a payment method for the storefront.
    """
    if method not in PaymentMethod.ALLOWED_METHODS:
        raise ValueError("Unsupported payment method.")
    pm, _ = PaymentMethod.objects.get_or_create(
        shop=shop,
        method=method,
        defaults={'tenant_id': shop.id}
    )
    pm.is_enabled = is_enabled
    pm.save()
    return pm

def ensure_shop_payment_methods(shop: Shop):
    """
    Ensure every allowed payment method exists for the shop.
    """
    for index, method in enumerate(PaymentMethod.ALLOWED_METHODS):
        PaymentMethod.objects.get_or_create(
            shop=shop,
            method=method,
            defaults={
                'tenant_id': shop.id,
                'display_order': index,
            },
        )

def get_storefront_payment_methods(shop: Shop):
    """
    Returns all enabled payment methods for the storefront, sorted by display_order.
    """
    return PaymentMethod.objects.filter(shop=shop, is_enabled=True).order_by('display_order')
