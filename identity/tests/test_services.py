from django.test import TestCase
from django.db import IntegrityError, transaction

from identity.models import (
    ContactPoint,
    ContactPointType,
    ContactPointLink,
    ContactPointLinkReason,
    CustomerContactPoint,
)
from identity.services import hashing
from identity.services.resolution import (
    get_or_create_contact_point,
    upsert_contact_point_link,
    upsert_customer_contact_point,
)
from shops.models import CustomerProfile, Shop
from users.models import PhoneIdentity, hash_text, normalize_phone


class HashingTests(TestCase):
    def test_canonicalize_phone_collapses_country_code_variants(self):
        # Local, international, and formatted forms → one canonical core.
        self.assertEqual(hashing.canonicalize_phone("01712345678"), "1712345678")
        self.assertEqual(hashing.canonicalize_phone("+880 1712-345678"), "1712345678")
        self.assertEqual(hashing.canonicalize_phone("8801712345678"), "1712345678")

    def test_hash_phone_identical_across_formatting(self):
        h1 = hashing.hash_phone("01712345678")
        h2 = hashing.hash_phone("+8801712345678")
        self.assertEqual(h1, h2)
        # 64-char sha256 hex, same primitive as users.hash_text.
        self.assertEqual(len(h1), 64)
        self.assertEqual(h1, hash_text("1712345678"))

    def test_phone_hash_is_stricter_than_phone_identity(self):
        # Documented design: ContactPoint PHONE hash != PhoneIdentity.phone_hash
        # for a country-code-prefixed number (canonical vs raw digits).
        raw = normalize_phone("+8801712345678")  # '8801712345678'
        self.assertNotEqual(hashing.hash_phone("+8801712345678"), hash_text(raw))

    def test_phone_suffix_matches_phone_identity_suffix(self):
        pi = PhoneIdentity.objects.create(phone_number="+8801712345678")
        self.assertEqual(hashing.phone_suffix("+8801712345678"), pi.phone_suffix)
        self.assertEqual(hashing.phone_suffix("01712345678"), "5678")

    def test_hash_address_matches_users_address_format(self):
        # Join-compatible with users.Address.save() composition.
        h = hashing.hash_address(street="House 5", city="Dhaka", postal_code="1207")
        expected = hash_text("house 5|dhaka|1207")
        self.assertEqual(h, expected)

    def test_hash_channel_identity_distinct_space(self):
        # PSID hashing only strips outer whitespace; opaque otherwise.
        self.assertEqual(
            hashing.hash_channel_identity(" psid_123 "), hash_text("psid_123")
        )

    def test_mask_phone(self):
        self.assertEqual(hashing.mask_phone("01712345678"), "*******5678")
        self.assertEqual(hashing.mask_phone(""), "")


class ContactPointDedupTests(TestCase):
    def test_get_or_create_dedups_by_type_and_hash(self):
        h = hashing.hash_phone("01712345678")
        cp1 = get_or_create_contact_point(
            point_type=ContactPointType.PHONE, value_hash=h, value_suffix="5678"
        )
        cp2 = get_or_create_contact_point(
            point_type=ContactPointType.PHONE, value_hash=h, value_suffix="5678"
        )
        self.assertEqual(cp1.id, cp2.id)
        self.assertEqual(ContactPoint.objects.count(), 1)

    def test_same_hash_different_type_are_distinct(self):
        h = hash_text("collision")
        cp_phone = get_or_create_contact_point(point_type=ContactPointType.PHONE, value_hash=h)
        cp_fb = get_or_create_contact_point(point_type=ContactPointType.FACEBOOK, value_hash=h)
        self.assertNotEqual(cp_phone.id, cp_fb.id)

    def test_unique_constraint_enforced_at_db(self):
        h = hash_text("x")
        ContactPoint.objects.create(point_type=ContactPointType.PHONE, value_hash=h)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ContactPoint.objects.create(point_type=ContactPointType.PHONE, value_hash=h)


class ContactPointLinkTests(TestCase):
    def _cp(self, seed: str) -> ContactPoint:
        return ContactPoint.objects.create(
            point_type=ContactPointType.PHONE, value_hash=hash_text(seed)
        )

    def test_link_collapses_ab_and_ba_into_one_row(self):
        a = self._cp("a")
        b = self._cp("b")
        link1 = upsert_contact_point_link(cp_a=a, cp_b=b)
        link2 = upsert_contact_point_link(cp_a=b, cp_b=a)  # reversed order
        self.assertEqual(link1.id, link2.id)
        self.assertEqual(ContactPointLink.objects.count(), 1)

    def test_canonical_ordering_source_lt_target(self):
        a = self._cp("a")
        b = self._cp("b")
        link = upsert_contact_point_link(cp_a=a, cp_b=b)
        self.assertLess(str(link.source_id), str(link.target_id))

    def test_repeated_link_increments_occurrence_and_confidence(self):
        a = self._cp("a")
        b = self._cp("b")
        link = upsert_contact_point_link(cp_a=a, cp_b=b)
        self.assertEqual(link.occurrence_count, 1)
        self.assertAlmostEqual(link.confidence_score, 0.5)
        link = upsert_contact_point_link(cp_a=b, cp_b=a)
        self.assertEqual(link.occurrence_count, 2)
        self.assertAlmostEqual(link.confidence_score, 0.6)

    def test_self_link_is_noop(self):
        a = self._cp("a")
        self.assertIsNone(upsert_contact_point_link(cp_a=a, cp_b=a))
        self.assertEqual(ContactPointLink.objects.count(), 0)


class CustomerContactPointTests(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(name="Demo", subdomain="demo")
        self.profile = CustomerProfile.objects.create(
            tenant_id=self.shop.id, phone_number="01712345678", name="Alice"
        )
        self.cp = ContactPoint.objects.create(
            point_type=ContactPointType.PHONE, value_hash=hash_text("a")
        )

    def test_upsert_creates_then_increments(self):
        link = upsert_customer_contact_point(
            customer_profile_id=str(self.profile.id), contact_point=self.cp
        )
        self.assertEqual(link.use_count, 1)
        self.assertIsNotNone(link.first_used_at)
        first_used = link.first_used_at

        link = upsert_customer_contact_point(
            customer_profile_id=str(self.profile.id), contact_point=self.cp
        )
        self.assertEqual(link.use_count, 2)
        self.assertEqual(link.first_used_at, first_used)  # unchanged
        self.assertEqual(CustomerContactPoint.objects.count(), 1)
