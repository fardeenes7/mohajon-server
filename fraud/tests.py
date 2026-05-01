from django.test import TestCase
from rest_framework.test import APIClient

from fraud.models import FraudEventType, FraudProfile, FraudTargetType
from fraud.services.fraud_scoring import apply_fraud_penalty
from fraud.services.identity_linking import link_identity_on_phone_claim
from orders.models import Order, OrderConfidenceLevel, OrderStatus
from shops.models import Shop
from users.models import PhoneIdentity, User


class FraudScoringTests(TestCase):
	def setUp(self):
		self.shop = Shop.objects.create(name="Demo Shop", subdomain="demo")
		self.actor = User.objects.create_user(email="actor@example.com", password="testpass123")
		self.phone_identity = PhoneIdentity.objects.create(user=self.actor, phone_number="+8801710000000")

	def _create_order(self, **kwargs):
		return Order.objects.create(
			shop=self.shop,
			tenant_id=self.shop.id,
			user=self.actor,
			phone_identity=self.phone_identity,
			status=OrderStatus.CONFIRMED,
			confidence_level=OrderConfidenceLevel.LOW,
			is_verified=False,
			actor_reference="127.0.0.1",
			**kwargs,
		)

	def test_unverified_order_does_not_affect_phone(self):
		order = self._create_order()
		apply_fraud_penalty(order, event_type=FraudEventType.DELIVERY_FAILED, base_penalty=10)

		actor_profile = FraudProfile.objects.get(
			target_type=FraudTargetType.USER,
			target_id=str(self.actor.id),
		)
		self.assertEqual(actor_profile.risk_score, 10)

		phone_profile = FraudProfile.objects.filter(
			target_type=FraudTargetType.PHONE,
			target_id=str(self.phone_identity.id),
		).first()
		self.assertIsNone(phone_profile)

	def test_verified_order_affects_phone(self):
		order = self._create_order(is_verified=True, confidence_level=OrderConfidenceLevel.HIGH)
		apply_fraud_penalty(order, event_type=FraudEventType.DELIVERY_FAILED, base_penalty=10)

		phone_profile = FraudProfile.objects.get(
			target_type=FraudTargetType.PHONE,
			target_id=str(self.phone_identity.id),
		)
		self.assertGreater(phone_profile.risk_score, 0)

	def test_reregistration_reuses_phone_history(self):
		profile = FraudProfile.objects.create(
			target_type=FraudTargetType.PHONE,
			target_id=str(self.phone_identity.id),
			phone_identity=self.phone_identity,
			risk_score=15,
		)
		new_user = User.objects.create_user(email="new@example.com", password="testpass123")
		link_identity_on_phone_claim(self.phone_identity, new_user)

		profile.refresh_from_db()
		self.assertEqual(profile.target_id, str(self.phone_identity.id))
		self.assertEqual(profile.risk_score, 15)


class CustomerProfileApiTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.shop = Shop.objects.create(name="Demo Shop", subdomain="demo")
		self.user = User.objects.create_user(email="user@example.com", password="testpass123")
		self.phone_identity = PhoneIdentity.objects.create(user=self.user, phone_number="+8801711111111")
		self.client.force_authenticate(self.user)

	def test_customer_profile_endpoint(self):
		order = Order.objects.create(
			shop=self.shop,
			tenant_id=self.shop.id,
			user=self.user,
			phone_identity=self.phone_identity,
			status=OrderStatus.CONFIRMED,
			confidence_level=OrderConfidenceLevel.LOW,
			is_verified=False,
			actor_reference="127.0.0.1",
		)
		response = self.client.get(f"/api/v1/customers/{self.user.id}/")
		self.assertEqual(response.status_code, 200)
		self.assertIn("basic_info", response.data)
		self.assertIn("fraud_info", response.data)
		self.assertIn("order_stats", response.data)
