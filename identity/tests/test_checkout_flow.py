"""
End-to-end integration tests for OrderIdentitySnapshot creation and async resolution.

KEY TESTING GOTCHA:
Django's TestCase wraps each test in a transaction that is NEVER committed, so
`transaction.on_commit` callbacks do NOT fire under CELERY_TASK_ALWAYS_EAGER.
We use `TestCase.captureOnCommitCallbacks()` (Django 4.1+) to execute on_commit
hooks synchronously within the test body, which is the correct solution for
this pattern.

Reference: https://docs.djangoproject.com/en/5.0/topics/testing/tools/#django.test.TestCase.captureOnCommitCallbacks
"""
from decimal import Decimal

from django.test import TestCase, override_settings

from catalog.models import Product, ProductVariant
from identity.models import (
    ContactPoint,
    ContactPointLink,
    ContactPointType,
    CustomerContactPoint,
    OrderIdentitySnapshot,
)
from identity.services import hashing
from identity.tasks import resolve_order_identity_graph
from orders.services.checkout import checkout_create_order
from shops.models import CustomerProfile, Shop


def _make_shop_and_product():
    """Helper: create a minimal shop + stocked product + variant."""
    shop = Shop.objects.create(name="Demo Shop", subdomain="demo-shop")
    product = Product.objects.create(
        shop=shop,
        tenant_id=shop.id,
        name="T-Shirt",
        slug="t-shirt",
        description="",
        status="PUBLISHED",
        base_price=Decimal("250.00"),
        compare_at_price=None,
        tax_rate=Decimal("0.0000"),
        sku="",
        is_digital=False,
        specifications={},
        seo_title="",
        seo_description="",
        sort_order=1,
    )
    variant = ProductVariant.objects.create(
        shop=shop,
        tenant_id=shop.id,
        product=product,
        sku="TSHIRT-RED-M",
        attribute_name_1="Color",
        attribute_value_1="Red",
        attribute_name_2="Size",
        attribute_value_2="M",
        stock_quantity=50,
        is_active=True,
        price_override=None,
    )
    return shop, product, variant


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class OrderIdentitySnapshotFlowTests(TestCase):
    """
    End-to-end tests for the identity graph pipeline:
      checkout_create_order → OrderIdentitySnapshot (sync) →
      resolve_order_identity_graph (async, fired via on_commit) →
      ContactPoint / CustomerContactPoint / ContactPointLink rows.

    on_commit callbacks are run synchronously using
    self.captureOnCommitCallbacks(execute=True) so they fire even though
    TestCase never really commits its wrapping transaction.
    """

    def setUp(self):
        self.shop, self.product, self.variant = _make_shop_and_product()

    def _items(self):
        return [{"product_id": str(self.product.id), "variant_id": str(self.variant.id), "quantity": 1}]

    def _checkout(self, **kwargs):
        """Run checkout_create_order with on_commit callbacks executing synchronously."""
        with self.captureOnCommitCallbacks(execute=True):
            order = checkout_create_order(
                shop_id=str(self.shop.id),
                items=self._items(),
                **kwargs,
            )
        return order

    # ── Step 3: Web checkout (no channel) ────────────────────────────────────

    def test_web_checkout_creates_snapshot_without_channel(self):
        """Web caller passes no shipping_address / channel — snapshot is created,
        resolver runs but finds no signals, still marks resolved_at."""
        order = self._checkout(payment_method="COD")

        snapshot = OrderIdentitySnapshot.objects.get(order=order)
        self.assertEqual(snapshot.channel, "")
        self.assertEqual(snapshot.phone_hash, "")
        # No signals → task resolved zero contact points, but still marks resolved.
        self.assertIsNotNone(snapshot.resolved_at)
        self.assertEqual(ContactPoint.objects.count(), 0)

    # ── Step 4 + 5: Chat checkout (full signals) ─────────────────────────────

    def test_chat_checkout_populates_graph_end_to_end(self):
        """Chat path with phone + address + PSID creates 3 ContactPoints, 3
        CustomerContactPoint junctions (profile present), and C(3,2)=3 undirected
        co-occurrence edges — all canonically ordered (source_id < target_id)."""
        profile = CustomerProfile.objects.create(
            tenant_id=self.shop.id, phone_number="01712345678", name="Alice"
        )
        order = self._checkout(
            payment_method="COD",
            customer_profile_id=str(profile.id),
            shipping_address={
                "name": "Alice",
                "phone": "+8801712345678",
                "address_line": "House 5, Road 2",
                "district": "Dhaka",
            },
            channel="FACEBOOK",
            channel_identity="psid_abc123",
        )

        snapshot = OrderIdentitySnapshot.objects.get(order=order)
        self.assertIsNotNone(snapshot.resolved_at)
        # Canonical phone hash (national form, no country code / trunk 0).
        self.assertEqual(snapshot.phone_hash, hashing.hash_phone("+8801712345678"))

        # PHONE + ADDRESS + FACEBOOK → 3 ContactPoints.
        self.assertEqual(ContactPoint.objects.count(), 3)
        self.assertIsNotNone(snapshot.phone_contact_point)
        self.assertIsNotNone(snapshot.address_contact_point)
        self.assertIsNotNone(snapshot.channel_contact_point)
        self.assertEqual(snapshot.channel_contact_point.point_type, ContactPointType.FACEBOOK)

        # Junctions for all 3 (profile present).
        self.assertEqual(CustomerContactPoint.objects.filter(customer_profile=profile).count(), 3)

        # C(3,2) = 3 co-occurrence edges, all canonically ordered.
        self.assertEqual(ContactPointLink.objects.count(), 3)
        for link in ContactPointLink.objects.all():
            self.assertLess(str(link.source_id), str(link.target_id))

    def test_resolution_is_idempotent(self):
        """Re-running the task for an already-resolved snapshot must be a no-op:
        counts do not grow and use_count is not double-counted."""
        profile = CustomerProfile.objects.create(
            tenant_id=self.shop.id, phone_number="01712345678", name="Alice"
        )
        order = self._checkout(
            payment_method="COD",
            customer_profile_id=str(profile.id),
            shipping_address={"name": "Alice", "phone": "01712345678", "address_line": "House 5", "district": "Dhaka"},
            channel="FACEBOOK",
            channel_identity="psid_abc123",
        )

        # Re-run the task explicitly — resolved_at guard makes it a no-op.
        resolve_order_identity_graph(str(order.id))

        self.assertEqual(ContactPoint.objects.count(), 3)
        self.assertEqual(ContactPointLink.objects.count(), 3)
        # use_count must be 1, not 2 (task did not double-count).
        # Must refresh snapshot so the resolved FK fields are not stale.
        order.identity_snapshot.refresh_from_db()
        phone_cp = order.identity_snapshot.phone_contact_point
        self.assertIsNotNone(phone_cp, "phone_contact_point should be resolved after task")
        ccp = CustomerContactPoint.objects.get(contact_point=phone_cp)
        self.assertEqual(ccp.use_count, 1)

    def test_two_orders_same_phone_reuse_node_and_strengthen_edges(self):
        """Two orders with identical signals share the same 3 ContactPoint nodes.
        Co-occurrence edges have occurrence_count=2 (at most), and the phone
        CustomerContactPoint junction has use_count=2."""
        profile = CustomerProfile.objects.create(
            tenant_id=self.shop.id, phone_number="01712345678", name="Alice"
        )
        addr = {"name": "Alice", "phone": "01712345678", "address_line": "House 5", "district": "Dhaka"}

        for _ in range(2):
            self._checkout(
                payment_method="COD",
                customer_profile_id=str(profile.id),
                shipping_address=addr,
                channel="FACEBOOK",
                channel_identity="psid_abc123",
            )

        # Same signals → same 3 nodes reused across both orders.
        self.assertEqual(ContactPoint.objects.count(), 3)
        self.assertEqual(ContactPointLink.objects.count(), 3)

        # Every edge seen twice → occurrence_count == 2 for all edges.
        for link in ContactPointLink.objects.all():
            self.assertEqual(link.occurrence_count, 2, f"Edge {link} should have count 2")

        # Phone junction usage counted twice.
        phone_cp = ContactPoint.objects.get(point_type=ContactPointType.PHONE)
        ccp = CustomerContactPoint.objects.get(customer_profile=profile, contact_point=phone_cp)
        self.assertEqual(ccp.use_count, 2)

    def test_chat_checkout_no_profile_still_creates_contact_points(self):
        """When there is no customer_profile_id (guest chat order), ContactPoints
        are still created and linked, but no CustomerContactPoint rows appear."""
        order = self._checkout(
            payment_method="COD",
            shipping_address={
                "name": "Bob",
                "phone": "01987654321",
                "address_line": "Road 3",
                "district": "Chittagong",
            },
            channel="FACEBOOK",
            channel_identity="psid_xyz999",
        )

        snapshot = OrderIdentitySnapshot.objects.get(order=order)
        self.assertIsNotNone(snapshot.resolved_at)
        self.assertEqual(ContactPoint.objects.count(), 3)
        self.assertEqual(CustomerContactPoint.objects.count(), 0)
        self.assertEqual(ContactPointLink.objects.count(), 3)
