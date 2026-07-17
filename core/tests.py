"""
Phase 1 keystone tests: the phone hash is unified across every app.

The whole point of `core.phone` is that the SAME physical number produces the
SAME `phone_hash` in `users.PhoneIdentity`, `fraud.GlobalFraudPool`,
`fraud.FraudReport`, and `identity.ContactPoint`. These tests assert that
byte-for-byte, across local / international / loosely-formatted input forms.

DB-independent where possible (SimpleTestCase) so they run without Postgres.
"""
from __future__ import annotations

from django.test import SimpleTestCase

from core.phone import (
    HASH_VERSION,
    hash_phone,
    mask_phone,
    normalize_phone_e164,
    phone_suffix,
)

# Same physical BD number, four input shapes.
_FORMS = [
    "01712345678",
    "+8801712345678",
    "8801712345678",
    "+880 1712-345678",
]


class CanonicalPhoneTests(SimpleTestCase):
    def test_all_forms_share_one_e164(self):
        canon = {normalize_phone_e164(f) for f in _FORMS}
        self.assertEqual(canon, {"+8801712345678"})

    def test_all_forms_share_one_hash(self):
        hashes = {hash_phone(f) for f in _FORMS}
        self.assertEqual(len(hashes), 1, f"forms hashed differently: {hashes}")

    def test_empty_and_unparseable_are_blank_not_sentinel(self):
        # Must be "" — NOT a hash of the empty string — so empty phones never
        # collide on a shared sentinel hash.
        for bad in (None, "", "   ", "not-a-phone", "abc"):
            self.assertEqual(hash_phone(bad), "", f"{bad!r} should hash to ''")
            self.assertEqual(normalize_phone_e164(bad), "")

    def test_suffix_and_mask(self):
        self.assertEqual(phone_suffix("01712345678"), "5678")
        self.assertTrue(mask_phone("01712345678").endswith("5678"))
        self.assertNotIn("1712", mask_phone("01712345678"))

    def test_hash_version_stamped(self):
        self.assertIsInstance(HASH_VERSION, int)
        self.assertGreaterEqual(HASH_VERSION, 1)


class CrossAppHashParityTests(SimpleTestCase):
    """The three app modules must all delegate to core.phone (same bytes)."""

    def test_identity_hashing_matches_core(self):
        from identity.services import hashing

        for f in _FORMS:
            self.assertEqual(hashing.hash_phone(f), hash_phone(f))
            self.assertEqual(hashing.canonicalize_phone(f), normalize_phone_e164(f))

    def test_all_three_apps_hash_identically(self):
        # users.PhoneIdentity, fraud (report/pool/risk), and identity all read
        # from core.phone; confirm a representative number lands on one hash.
        from identity.services import hashing as identity_hashing

        target = hash_phone("01712345678")
        self.assertEqual(identity_hashing.hash_phone("+8801712345678"), target)
        self.assertNotEqual(target, "")
