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
            "status": CourierConsignmentStatus.DELIVERED if payload.get("status") == "success" else CourierConsignmentStatus.FAILED,
            "tracking_code": payload.get("tracking_code", ""),
            "success_rate": payload.get("success_rate"),
        }

    def create_consignment(self, shop_id: str, order: dict) -> dict:
        return {
            "external_consignment_id": "DUMMY-CN-1",
            "tracking_code": "DUMMY-CN-1",
            "status": CourierConsignmentStatus.CREATED,
            "payload": {},
        }

    def calculate_price(self, shop_id: str, price_request: dict) -> dict:
        return {"price": 60}

    def list_cities(self, shop_id: str) -> list[dict]:
        return [{"id": 1, "name": "Dhaka"}]

    def list_zones(self, shop_id: str, city_id: int) -> list[dict]:
        return [{"id": 1, "name": "Zone 1"}]

    def list_areas(self, shop_id: str, zone_id: int) -> list[dict]:
        return [{"id": 1, "name": "Area 1"}]

    def get_tracking(self, shop_id: str, external_consignment_id: str) -> dict:
        return {
            "status": CourierConsignmentStatus.IN_TRANSIT,
            "tracking_code": external_consignment_id,
            "payload": {},
        }

    def verify_webhook_signature(self, shop_id, signature, body) -> bool:
        from webhooks.services import webhook_signature_valid
        return webhook_signature_valid(signature=signature, body=body)

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


class PathaoAdapterTestCase(TestCase):
    def setUp(self):
        from shipping.couriers.pathao.adapter import PathaoCourier
        self.adapter = PathaoCourier()

    def test_provider_code(self):
        self.assertEqual(self.adapter.provider_code, "PATHAO")

    def test_registered_in_registry(self):
        # Populated by ShippingConfig.ready() at app load.
        self.assertIsNotNone(courier_registry.get_provider("PATHAO"))

    def test_parse_webhook_maps_status_slug(self):
        parsed = self.adapter.parse_webhook_payload({
            "consignment_id": "DA123",
            "merchant_order_id": "ORDER-9",
            "status": "Delivered",
            "status_slug": "delivered",
        })
        self.assertEqual(parsed["external_consignment_id"], "DA123")
        self.assertEqual(parsed["order_id"], "ORDER-9")
        self.assertEqual(parsed["status"], CourierConsignmentStatus.DELIVERED)

    def test_parse_webhook_unknown_slug_is_blank(self):
        parsed = self.adapter.parse_webhook_payload({
            "consignment_id": "DA123",
            "merchant_order_id": "ORDER-9",
            "status_slug": "some_new_status",
        })
        self.assertEqual(parsed["status"], "")

    def test_create_consignment_translates_dto(self):
        from unittest.mock import MagicMock
        fake_client = MagicMock()
        fake_client.store_id = "STORE-1"
        fake_client.create_order.return_value = {"data": {"consignment_id": "DA999"}}

        with patch.object(self.adapter, "_client", return_value=fake_client):
            result = self.adapter.create_consignment("shop-1", {
                "order_id": "o1",
                "merchant_order_id": "o1",
                "recipient_name": "Karim",
                "recipient_phone": "+8801700000000",
                "recipient_address": "123 Road, Dhaka",
                "amount_to_collect": 500,
                "item_quantity": 2,
                "item_weight": 1.0,
            })

        self.assertEqual(result["external_consignment_id"], "DA999")
        self.assertEqual(result["status"], CourierConsignmentStatus.CREATED)
        sent = fake_client.create_order.call_args.args[0]
        self.assertEqual(sent["store_id"], "STORE-1")
        self.assertEqual(sent["amount_to_collect"], 500)
        self.assertEqual(sent["delivery_type"], 48)

    def test_create_consignment_requires_store_id(self):
        from unittest.mock import MagicMock
        from shipping.couriers.pathao.client import PathaoError
        fake_client = MagicMock()
        fake_client.store_id = ""
        with patch.object(self.adapter, "_client", return_value=fake_client):
            with self.assertRaises(PathaoError):
                self.adapter.create_consignment("shop-1", {"order_id": "o1"})


class PathaoClientTokenTestCase(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        from shipping.couriers.pathao.client import PathaoClient
        self.client = PathaoClient(
            shop_id="shop-token",
            credentials={
                "client_id": "cid",
                "client_secret": "secret",
                "username": "u@x.com",
                "password": "pw",
            },
            is_test_mode=True,
        )

    def test_get_access_token_caches_and_reuses(self):
        with patch.object(
            self.client, "_issue_token",
            return_value={"access_token": "AT1", "expires_in": 4320, "refresh_token": "RT1"},
        ) as mock_issue:
            first = self.client.get_access_token()
            second = self.client.get_access_token()

        self.assertEqual(first, "AT1")
        self.assertEqual(second, "AT1")
        mock_issue.assert_called_once()  # second call served from cache

    def test_sandbox_base_url(self):
        self.assertIn("sandbox", self.client.base_url)


class CourierCredentialsServiceTestCase(TestCase):
    def setUp(self):
        plan = SubscriptionPlan.objects.create(name="FREE")
        self.shop = Shop.objects.create(name="Cred Shop", subdomain="credshop", plan=plan)

    def test_set_and_get_credentials_roundtrip(self):
        from shipping.services import set_courier_credentials, get_courier_credentials
        set_courier_credentials(
            shop_id=str(self.shop.id),
            provider="PATHAO",
            credentials={"client_id": "abc", "client_secret": "xyz"},
            is_test_mode=True,
            default_store_id="STORE-7",
        )
        creds = get_courier_credentials(shop_id=str(self.shop.id), provider="PATHAO")
        self.assertEqual(creds["client_id"], "abc")

    def test_get_credentials_missing_returns_none(self):
        from shipping.services import get_courier_credentials
        self.assertIsNone(
            get_courier_credentials(shop_id=str(self.shop.id), provider="PATHAO")
        )
