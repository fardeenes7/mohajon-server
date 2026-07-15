from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from catalog.models import Category, Product, ProductStatus, ProductVariant
from catalog.selectors.product import product_list_for_storefront
from shops.models import Shop

User = get_user_model()


class CatalogBaseTestCase(TestCase):
    def setUp(self):
        # Create users
        self.user_a = User.objects.create_user(email="user_a@example.com", password="password")
        self.user_b = User.objects.create_user(email="user_b@example.com", password="password")

        # Create shops
        self.shop_a = Shop.objects.create(name="Shop A", subdomain="shop-a")
        self.shop_b = Shop.objects.create(name="Shop B", subdomain="shop-b")
        
        from shops.models import ShopMember
        ShopMember.objects.create(user=self.user_a, shop=self.shop_a, role="OWNER")
        ShopMember.objects.create(user=self.user_b, shop=self.shop_b, role="OWNER")

        # Clients setup
        self.client_a = APIClient()
        from rest_framework_simplejwt.tokens import RefreshToken
        token = RefreshToken.for_user(self.user_a).access_token
        self.client_a.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        # Mock tenant_id header which the view might expect (using X-Tenant-ID or middleware injects it)
        # Assuming the middleware injects request.tenant_id based on a header. 
        # Wait, the views do: getattr(request, "tenant_id", None)
        # We might need to mock _require_tenant or ensure tenant_id is set.
        # Actually, in Django REST framework, middleware doesn't easily inject on APIClient requests if we don't pass headers,
        # but let's see how `_require_tenant` extracts it. The code says: `getattr(request, "tenant_id", None)`.
        # To bypass this in tests, we can patch `_require_tenant` or add a middleware. Let's patch it.

        # Let's create some basic data
        self.cat_a = Category.objects.create(shop=self.shop_a, tenant_id=self.shop_a.id, name="Cat A")
        self.cat_b = Category.objects.create(shop=self.shop_b, tenant_id=self.shop_b.id, name="Cat B")

        self.prod_a = Product.objects.create(
            shop=self.shop_a, tenant_id=self.shop_a.id, category=self.cat_a, name="Product A", slug="prod-a",
            base_price=Decimal("10.00"), status=ProductStatus.PUBLISHED
        )
        self.prod_b = Product.objects.create(
            shop=self.shop_b, tenant_id=self.shop_b.id, category=self.cat_b, name="Product B", slug="prod-b",
            base_price=Decimal("20.00"), status=ProductStatus.PUBLISHED
        )
        
        self.var_a = ProductVariant.objects.create(
            shop=self.shop_a, tenant_id=self.shop_a.id, product=self.prod_a, sku="SKU-A"
        )
        self.var_b = ProductVariant.objects.create(
            shop=self.shop_b, tenant_id=self.shop_b.id, product=self.prod_b, sku="SKU-B"
        )
        
        from shops.models import StockLocation
        StockLocation.objects.create(shop=self.shop_a, tenant_id=self.shop_a.id, name="Main", is_default=True)
        StockLocation.objects.create(shop=self.shop_b, tenant_id=self.shop_b.id, name="Main", is_default=True)


class TestShopIsolation(CatalogBaseTestCase):
    @patch("catalog.api.views._require_tenant")
    def test_shop_a_cannot_access_shop_b_products(self, mock_tenant):
        mock_tenant.return_value = self.shop_a.id
        
        # Test List
        response = self.client_a.get("/api/v1/catalog/products/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], str(self.prod_a.id))

        # Test Retrieve (should 404 for Shop B's product)
        response = self.client_a.get(f"/api/v1/catalog/products/{self.prod_b.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # Test Update (should 404/400 for Shop B's product)
        response = self.client_a.patch(f"/api/v1/catalog/products/{self.prod_b.id}/", {"name": "Hacked"})
        # partial_update might catch it in selector/service and raise 404 or ValueError
        self.assertIn(response.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST])

    @patch("catalog.api.views._require_tenant")
    def test_shop_a_cannot_access_shop_b_categories(self, mock_tenant):
        mock_tenant.return_value = self.shop_a.id
        response = self.client_a.get("/api/v1/catalog/categories/")
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], str(self.cat_a.id))

        response = self.client_a.get(f"/api/v1/catalog/categories/{self.cat_b.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class TestFTSFallback(CatalogBaseTestCase):
    def test_search_vector_populated_on_save(self):
        prod = Product.objects.create(
            shop=self.shop_a, tenant_id=self.shop_a.id, name="Apple iPhone 15", description="Latest Apple smartphone", base_price=Decimal("999.00")
        )
        # Search vector is updated via signal, so we need to refresh from db
        prod.refresh_from_db()
        self.assertIsNotNone(prod.search_vector)
        # Check if it has 'apple' in it by doing a filter
        qs = Product.objects.filter(search_vector="apple")
        self.assertTrue(qs.filter(id=prod.id).exists())

    def test_storefront_search_fts(self):
        
        # We need a product to search
        prod = Product.objects.create(
            shop=self.shop_a, tenant_id=self.shop_a.id, name="Galaxy S24 Ultra", description="Samsung flagship", base_price=Decimal("1200.00"), status=ProductStatus.PUBLISHED
        )
        prod.refresh_from_db()
        
        qs = product_list_for_storefront(shop_id=self.shop_a.id, search="galaxy")
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().id, prod.id)


class TestProductCRUD(CatalogBaseTestCase):
    @patch("catalog.api.views._require_tenant")
    def test_create_product(self, mock_tenant):
        mock_tenant.return_value = self.shop_a.id
        data = {
            "name": "New Product",
            "base_price": "50.00",
            "category_id": str(self.cat_a.id)
        }
        response = self.client_a.post("/api/v1/catalog/products/", data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "New Product")

    @patch("catalog.api.views._require_tenant")
    def test_update_product(self, mock_tenant):
        mock_tenant.return_value = self.shop_a.id
        data = {"name": "Updated Name"}
        response = self.client_a.patch(f"/api/v1/catalog/products/{self.prod_a.id}/", data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Updated Name")


class TestVariantCRUDAndStock(CatalogBaseTestCase):
    @patch("catalog.api.views._require_tenant")
    def test_adjust_stock_increase(self, mock_tenant):
        mock_tenant.return_value = self.shop_a.id
        data = {
            "delta": 10,
            "reason": "ADJUSTMENT"
        }
        response = self.client_a.post(f"/api/v1/catalog/products/{self.prod_a.id}/variants/{self.var_a.id}/adjust-stock/", data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        self.var_a.refresh_from_db()
        self.assertEqual(self.var_a.stock_quantity, 10)

    @patch("catalog.api.views._require_tenant")
    def test_create_variant_with_attributes(self, mock_tenant):
        mock_tenant.return_value = self.shop_a.id
        data = {
            "attribute_name_1": "Color",
            "attribute_value_1": "Red",
            "attribute_name_2": "Size",
            "attribute_value_2": "L",
            "price_override": "15.00",
            "weight_override_grams": 500,
            "sku": "RED-L"
        }
        response = self.client_a.post(f"/api/v1/catalog/products/{self.prod_a.id}/variants/", data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Verify model overrides behavior
        variant = ProductVariant.objects.get(id=response.data["id"])
        self.assertEqual(variant.effective_price, Decimal("15.00"))
        self.assertEqual(variant.effective_weight, 500)
        self.assertEqual(variant.product_id, self.prod_a.id)

        # Verify shop isolation (can't add variant to shop_b's product)
        # We test actual behavior - if it successfully creates, that might be a leakage bug.
        response_isolated = self.client_a.post(f"/api/v1/catalog/products/{self.prod_b.id}/variants/", data, format="json")
        self.assertEqual(response_isolated.status_code, status.HTTP_400_BAD_REQUEST, "Shop A should not be able to add a variant to Shop B's product")

    @patch("catalog.api.views._require_tenant")
    def test_adjust_stock_decrease_below_zero(self, mock_tenant):
        mock_tenant.return_value = self.shop_a.id
        # First add some stock
        self.client_a.post(f"/api/v1/catalog/products/{self.prod_a.id}/variants/{self.var_a.id}/adjust-stock/", {"delta": 10, "reason": "RESTOCK"}, format="json")
        
        # Now try to decrease it below zero
        data = {
            "delta": -20,
            "reason": "ADJUSTMENT"
        }
        response = self.client_a.post(f"/api/v1/catalog/products/{self.prod_a.id}/variants/{self.var_a.id}/adjust-stock/", data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Insufficient stock", str(response.data))
class TestStorefrontVisibility(CatalogBaseTestCase):
    def test_draft_product_not_visible_in_storefront(self):
        draft_prod = Product.objects.create(
            shop=self.shop_a, tenant_id=self.shop_a.id, name="Draft Product", slug="draft-prod",
            base_price=Decimal("10.00"), status=ProductStatus.DRAFT
        )
        
        qs = product_list_for_storefront(shop_id=self.shop_a.id)
        # Should contain prod_a but not draft_prod
        self.assertTrue(qs.filter(id=self.prod_a.id).exists())
        self.assertFalse(qs.filter(id=draft_prod.id).exists())


class TestAIFacadeEndpoints(CatalogBaseTestCase):
    @patch("catalog.api.views._require_tenant")
    @patch("ai.services.generate_description")
    def test_ai_generate_description(self, mock_ai, mock_tenant):
        mock_tenant.return_value = self.shop_a.id
        mock_ai.return_value = "<p>Amazing product!</p>"
        
        data = {
            "name": "Smart Watch",
            "specifications": {"Color": "Black"}
        }
        response = self.client_a.post("/api/v1/catalog/products/ai-generate-description/", data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["description"], "<p>Amazing product!</p>")

class TestTenantSpoofing(CatalogBaseTestCase):
    def test_cross_tenant_spoofing_blocked(self):
        # user_a is only a member of shop_a.
        # They try to access shop_b's products by explicitly setting X-Tenant-ID.
        # Before the fix, this succeeds and returns shop_b's data.
        # After the fix, it should return 403 Forbidden.
        response = self.client_a.get(
            "/api/v1/catalog/products/",
            HTTP_X_TENANT_ID=str(self.shop_b.id)
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
