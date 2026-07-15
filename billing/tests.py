from django.test import TestCase

# Create your tests here.

class AIUsageLogViewSetTestCase(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient
        from shops.models import Shop, SubscriptionPlan
        import uuid
        self.client = APIClient()
        plan = SubscriptionPlan.objects.create(name="FREE")
        self.shop1 = Shop.objects.create(id=uuid.uuid4(), name="Shop 1", subdomain="shop1", plan=plan)
        self.shop2 = Shop.objects.create(id=uuid.uuid4(), name="Shop 2", subdomain="shop2", plan=plan)
        
        from users.models import User
        self.user1 = User.objects.create_user(email="user1@test.com", password="password")
        self.user2 = User.objects.create_user(email="user2@test.com", password="password")
        
        from shops.models import ShopMember
        ShopMember.objects.create(shop=self.shop1, user=self.user1, role="ADMIN")
        ShopMember.objects.create(shop=self.shop2, user=self.user2, role="ADMIN")
        
        from ai.models import AIUsageLog
        AIUsageLog.objects.create(
            shop=self.shop1, tenant_id=self.shop1.id, usage_type="CHAT_COMPLETION", provider="OPENAI", model_name="gpt-4o"
        )
        AIUsageLog.objects.create(
            shop=self.shop2, tenant_id=self.shop2.id, usage_type="CHAT_COMPLETION", provider="OPENAI", model_name="gpt-4o"
        )
        
        self.url = "/api/v1/billing/ai-usage/"

    def test_list_logs_tenant_isolation(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.get(self.url, HTTP_X_TENANT_ID=str(self.shop1.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        
        self.client.force_authenticate(user=self.user2)
        response = self.client.get(self.url, HTTP_X_TENANT_ID=str(self.shop2.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)

    def test_read_only(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.post(self.url, {"usage_type": "CHAT_COMPLETION"}, HTTP_X_TENANT_ID=str(self.shop1.id))
        self.assertEqual(response.status_code, 405) # Method Not Allowed
