from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
import json
import hmac
from hashlib import sha256
from unittest.mock import patch

from shops.models import Shop, SubscriptionPlan
from orders.models import Order, OrderStatus
from shipping.registry import CourierInterface, courier_registry
from shipping.models import CourierConsignment, CourierConsignmentStatus

class DummyCourierAdapter(CourierInterface):
    @property
    def provider_code(self) -> str:
        return "DUMMY"
        
    def parse_webhook_payload(self, payload: dict) -> dict:
        return {
            "external_consignment_id": str(payload.get("id")),
            "order_id": str(payload.get("order_id")),
            "status": CourierConsignmentStatus.DELIVERED if payload.get("status") == "success" else CourierConsignmentStatus.CANCELLED,
            "tracking_code": payload.get("tracking_code", ""),
            "success_rate": payload.get("success_rate"),
        }

class CourierWebhookTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("courier-webhook", kwargs={"provider_code": "DUMMY"})
        self.app_secret = "test_secret_12345"
        
        # Setup dummy provider
        self.adapter = DummyCourierAdapter()
        courier_registry.register(self.adapter)
        
        # Setup test data
        plan = SubscriptionPlan.objects.create(name="FREE")
        self.shop = Shop.objects.create(name="Test Shop", subdomain="testshop", plan=plan)
        from users.models import PhoneIdentity
        self.phone = PhoneIdentity.objects.create(phone_number="+8801700000000")
        self.order = Order.objects.create(
            shop=self.shop,
            tenant_id=self.shop.id,
            status=OrderStatus.IN_TRANSIT,
            phone_identity=self.phone,
        )
        self.consignment = CourierConsignment.objects.create(
            shop=self.shop,
            order=self.order,
            provider="DUMMY",
            external_consignment_id="12345",
            status=CourierConsignmentStatus.DISPATCHED,
        )

    def tearDown(self):
        courier_registry._providers.pop("DUMMY", None)

    @override_settings(META_APP_SECRET="test_secret_12345")
    @patch("orders.selectors.orders.get_order_terminal_person_id")
    @patch("identity.services.behavioral.ingest_courier_success_rate")
    def test_webhook_valid_signature_updates_order_and_ingests_rate(self, mock_ingest, mock_get_person):
        mock_get_person.return_value = "dummy-person-uuid"
        
        payload_data = {
            "id": "12345",
            "order_id": str(self.order.id),
            "status": "success",
            "tracking_code": "TRK123",
            "success_rate": 95.5,
            "provider": "DUMMY"
        }
        payload = json.dumps(payload_data).encode('utf-8')
        signature = hmac.new(self.app_secret.encode('utf-8'), msg=payload, digestmod=sha256).hexdigest()
        
        response = self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=f"sha256={signature}"
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify order status was updated
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.DELIVERED)
        
        # Verify consignment status was updated
        self.consignment.refresh_from_db()
        self.assertEqual(self.consignment.status, CourierConsignmentStatus.DELIVERED)
        
        # Verify identity service called
        mock_ingest.assert_called_once_with(
            person_id="dummy-person-uuid",
            rate=95.5
        )

    @override_settings(META_APP_SECRET="test_secret_12345")
    @patch("orders.selectors.orders.get_order_terminal_person_id")
    @patch("identity.services.behavioral.ingest_courier_success_rate")
    def test_webhook_invalid_signature_is_forbidden(self, mock_ingest, mock_get_person):
        payload_data = {
            "id": "12345",
            "order_id": str(self.order.id),
            "status": "success",
            "provider": "DUMMY"
        }
        payload = json.dumps(payload_data).encode('utf-8')
        invalid_signature = "bad_signature"
        
        response = self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=f"sha256={invalid_signature}"
        )
        
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        
        # Verify nothing was updated
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.IN_TRANSIT)
        
        mock_ingest.assert_not_called()
