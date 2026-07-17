"""
Tests for Layer 3 Person / Household resolution (design doc §6 evidence tiers).

These exercise identity.services.resolution directly against resolved
OrderIdentitySnapshot rows — the same shape the async task hands to
attribute_snapshot_to_person — so they cover the resolver logic without dragging
in the full checkout stack. The two design-doc done-checks are the headline
cases:

  1. Same verified phone + name + address across two orders → ONE Person.
  2. Same phone, DIFFERENT name across two orders → TWO Persons, ONE Household.

Plus merged_into terminal resolution and backfill idempotency.
"""
from __future__ import annotations

from django.test import TestCase

from identity.models import (
    ContactPoint,
    ContactPointType,
    Household,
    OrderIdentitySnapshot,
    Person,
)
from identity.services import hashing, resolution
from orders.models import Order
from shops.models import Shop
from users.models import hash_text


def _phone_cp(raw: str) -> ContactPoint:
    return resolution.get_or_create_contact_point(
        point_type=ContactPointType.PHONE,
        value_hash=hashing.hash_phone(raw),
        value_suffix=hashing.phone_suffix(raw),
        display_value=hashing.mask_phone(raw),
    )


def _address_cp(seed: str) -> ContactPoint:
    return resolution.get_or_create_contact_point(
        point_type=ContactPointType.ADDRESS,
        value_hash=hash_text(seed),
        display_value=seed,
    )


class PersonResolutionTestBase(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(name="Demo", subdomain="demo")

    def _order(self, *, is_verified: bool = False, verification_method: str = "NONE") -> Order:
        return Order.objects.create(
            shop=self.shop,
            tenant_id=self.shop.id,
            is_verified=is_verified,
            verification_method=verification_method,
        )

    def _snapshot(
        self,
        *,
        order: Order,
        phone_cp: ContactPoint | None = None,
        address_cp: ContactPoint | None = None,
        channel_cp: ContactPoint | None = None,
        display_name: str = "",
        channel: str = "",
        channel_identity: str = "",
    ) -> OrderIdentitySnapshot:
        """A snapshot already through step 1-4 of the task: resolved FKs set."""
        return OrderIdentitySnapshot.objects.create(
            order=order,
            phone_contact_point=phone_cp,
            address_contact_point=address_cp,
            channel_contact_point=channel_cp,
            display_name=display_name,
            channel=channel,
            channel_identity=channel_identity,
            phone_hash=phone_cp.value_hash if phone_cp else "",
            address_hash=address_cp.value_hash if address_cp else "",
        )


class TerminalResolutionTests(PersonResolutionTestBase):
    def test_follows_merged_into_to_terminal(self):
        a = Person.objects.create(display_name="A")
        b = Person.objects.create(display_name="B")
        c = Person.objects.create(display_name="C")
        # a → b → c chain of merges.
        a.merged_into = b
        a.save(update_fields=["merged_into"])
        b.merged_into = c
        b.save(update_fields=["merged_into"])

        self.assertEqual(resolution.resolve_terminal_person(a).id, c.id)
        self.assertEqual(resolution.resolve_terminal_person(b).id, c.id)
        self.assertEqual(resolution.resolve_terminal_person(c).id, c.id)

    def test_cycle_does_not_hang(self):
        a = Person.objects.create()
        b = Person.objects.create()
        a.merged_into = b
        a.save(update_fields=["merged_into"])
        b.merged_into = a  # corrupt A→B→A cycle
        b.save(update_fields=["merged_into"])
        # Must terminate (bounded hops), returning one of the two nodes.
        terminal = resolution.resolve_terminal_person(a)
        self.assertIn(terminal.id, {a.id, b.id})


class GetOrCreatePersonTests(PersonResolutionTestBase):
    def test_new_phone_cp_mints_person_with_primary_phone(self):
        cp = _phone_cp("01712345678")
        person = resolution.get_or_create_person_for_contact_point(cp, display_name="Alice")
        cp.refresh_from_db()
        self.assertEqual(cp.person_id, person.id)
        self.assertEqual(person.primary_phone_cp_id, cp.id)
        self.assertEqual(person.display_name, "Alice")

    def test_owned_cp_returns_terminal_person(self):
        cp = _phone_cp("01712345678")
        p1 = resolution.get_or_create_person_for_contact_point(cp)
        # Merge p1 away; the CP still points at p1 (stale alias).
        winner = Person.objects.create()
        p1.merged_into = winner
        p1.save(update_fields=["merged_into"])
        cp.refresh_from_db()
        p2 = resolution.get_or_create_person_for_contact_point(cp)
        self.assertEqual(p2.id, winner.id)


class MergePersonsTests(PersonResolutionTestBase):
    def test_oldest_wins_and_signals_reparent(self):
        winner = Person.objects.create(display_name="")
        loser = Person.objects.create(display_name="Alice")
        cp = _phone_cp("01712345678")
        ContactPoint.objects.filter(pk=cp.pk).update(person=loser)

        result = resolution.merge_persons(loser, winner)  # arg order irrelevant

        self.assertEqual(result.id, winner.id)  # older wins
        cp.refresh_from_db()
        loser.refresh_from_db()
        self.assertEqual(cp.person_id, winner.id)  # signal reparented
        self.assertEqual(loser.merged_into_id, winner.id)  # append-only tombstone
        result.refresh_from_db()
        self.assertEqual(result.display_name, "Alice")  # best name propagated

    def test_self_merge_is_noop(self):
        p = Person.objects.create()
        self.assertEqual(resolution.merge_persons(p, p).id, p.id)


class HouseholdTests(PersonResolutionTestBase):
    def test_neither_has_household_creates_one(self):
        a = Person.objects.create()
        b = Person.objects.create()
        h = resolution.link_persons_into_household(a, b)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertIsNotNone(h)
        self.assertEqual(a.household_id, h.id)
        self.assertEqual(b.household_id, h.id)

    def test_one_has_household_other_joins(self):
        h = Household.objects.create()
        a = Person.objects.create(household=h)
        b = Person.objects.create()
        result = resolution.link_persons_into_household(a, b)
        b.refresh_from_db()
        self.assertEqual(result.id, h.id)
        self.assertEqual(b.household_id, h.id)


class EvidenceTierDoneChecks(PersonResolutionTestBase):
    """The two headline done-checks from the design doc."""

    def test_same_phone_name_address_resolves_to_one_person(self):
        phone = _phone_cp("01712345678")
        addr = _address_cp("house 5|dhaka|")

        o1 = self._order(is_verified=True, verification_method="OTP")
        s1 = self._snapshot(
            order=o1, phone_cp=phone, address_cp=addr, display_name="Alice"
        )
        p1 = resolution.attribute_snapshot_to_person(s1)

        o2 = self._order(is_verified=True, verification_method="OTP")
        s2 = self._snapshot(
            order=o2, phone_cp=phone, address_cp=addr, display_name="Alice"
        )
        p2 = resolution.attribute_snapshot_to_person(s2)

        self.assertEqual(p1.id, p2.id)
        self.assertEqual(Person.objects.count(), 1)
        self.assertTrue(p2.is_verified)

    def test_shared_phone_different_name_two_persons_one_household(self):
        phone = _phone_cp("01712345678")
        addr_alice = _address_cp("house 5|dhaka|")
        addr_bob = _address_cp("road 3|chittagong|")

        o1 = self._order()
        s1 = self._snapshot(
            order=o1, phone_cp=phone, address_cp=addr_alice, display_name="Alice"
        )
        p1 = resolution.attribute_snapshot_to_person(s1)

        o2 = self._order()
        s2 = self._snapshot(
            order=o2, phone_cp=phone, address_cp=addr_bob, display_name="Bob"
        )
        p2 = resolution.attribute_snapshot_to_person(s2)

        self.assertNotEqual(p1.id, p2.id)  # distinct Persons
        self.assertEqual(Person.objects.count(), 2)
        p1.refresh_from_db()
        p2.refresh_from_db()
        self.assertIsNotNone(p1.household_id)
        self.assertEqual(p1.household_id, p2.household_id)  # same Household
        self.assertEqual(Household.objects.count(), 1)

    def test_attribution_is_idempotent(self):
        phone = _phone_cp("01712345678")
        addr = _address_cp("house 5|dhaka|")
        o1 = self._order(is_verified=True, verification_method="OTP")
        s1 = self._snapshot(order=o1, phone_cp=phone, address_cp=addr, display_name="Alice")

        first = resolution.attribute_snapshot_to_person(s1)
        # Re-run the SAME snapshot: ownership already set → no new Person.
        second = resolution.attribute_snapshot_to_person(s1)
        self.assertEqual(first.id, second.id)
        self.assertEqual(Person.objects.count(), 1)


class BackfillTests(PersonResolutionTestBase):
    def _seed_two_orders_shared_phone(self):
        phone = _phone_cp("01712345678")
        addr_a = _address_cp("house 5|dhaka|")
        addr_b = _address_cp("road 3|chittagong|")
        o1 = self._order()
        self._snapshot(order=o1, phone_cp=phone, address_cp=addr_a, display_name="Alice")
        o2 = self._order()
        self._snapshot(order=o2, phone_cp=phone, address_cp=addr_b, display_name="Bob")
        # Mark resolved so the backfill picks them up.
        OrderIdentitySnapshot.objects.update(resolved_at=self.shop.created_at)

    def test_backfill_builds_persons_and_is_idempotent(self):
        self._seed_two_orders_shared_phone()

        first = resolution.backfill_persons_from_snapshots()
        persons_after_first = Person.objects.count()
        households_after_first = Household.objects.count()
        self.assertEqual(first["processed"], 2)
        self.assertEqual(persons_after_first, 2)  # shared phone, distinct names
        self.assertEqual(households_after_first, 1)

        # Second run must not create duplicates or double-merge.
        second = resolution.backfill_persons_from_snapshots()
        self.assertEqual(second["processed"], 2)
        self.assertEqual(second["persons_created"], 0)
        self.assertEqual(Person.objects.count(), persons_after_first)
        self.assertEqual(Household.objects.count(), households_after_first)

    def test_backfill_skips_unresolved_snapshots(self):
        o = self._order()
        # No resolved CPs and resolved_at stays null → not eligible.
        self._snapshot(order=o, display_name="Nobody")
        result = resolution.backfill_persons_from_snapshots()
        self.assertEqual(result["processed"], 0)
        self.assertEqual(Person.objects.count(), 0)
