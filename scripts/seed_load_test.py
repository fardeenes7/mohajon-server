import os
import django
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mohajon.settings')
django.setup()

from django.contrib.auth import get_user_model
from shops.models import Shop, ShopMember, SubscriptionPlan
from catalog.models import Product, ProductVariant
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()

def seed_load_test_data():
    plan, _ = SubscriptionPlan.objects.get_or_create(name='PRO', defaults={'max_products': 1000})
    
    user, created = User.objects.get_or_create(
        email='loadtest@example.com',
        defaults={'first_name': 'Load', 'last_name': 'Tester'}
    )
    if created:
        user.set_password('loadtest_password')
        user.save()
        
    shop, created = Shop.objects.get_or_create(
        subdomain='loadtest',
        defaults={'name': 'Load Test Shop', 'plan': plan}
    )
    
    ShopMember.objects.get_or_create(
        user=user,
        shop=shop,
        defaults={'role': 'OWNER'}
    )
    
    product, created = Product.objects.get_or_create(
        shop=shop,
        slug='load-test-product',
        defaults={
            'name': 'Load Test Product',
            'tenant_id': shop.id,
            'base_price': Decimal('100.00'),
            'status': 'PUBLISHED'
        }
    )
    
    variant, created = ProductVariant.objects.get_or_create(
        product=product,
        sku='LT-001',
        defaults={
            'shop': shop,
            'tenant_id': shop.id,
            'stock_quantity': 1000, # Enough for 50 req/s for 20s
            'is_active': True
        }
    )
    if not created:
        variant.stock_quantity = 1000
        variant.save()
    
    refresh = RefreshToken.for_user(user)
    token = str(refresh.access_token)
    
    print(f"SHOP_ID={shop.id}")
    print(f"PRODUCT_ID={product.id}")
    print(f"VARIANT_ID={variant.id}")
    print(f"AUTH_TOKEN={token}")

if __name__ == '__main__':
    seed_load_test_data()
