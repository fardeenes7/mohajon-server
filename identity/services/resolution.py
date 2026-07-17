"""
Pure, unit-testable resolution helpers for the identity graph.

Every write goes through get_or_create / update_or_create keyed by stable
identifiers (hashes, order ids, canonical edge ordering) so the async
orchestrator can be retried or re-run without producing duplicates or
double-counting.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from identity.models import (
    ChannelActor,
    ChannelActorChannel,
    ContactPoint,
    ContactPointLink,
    ContactPointLinkReason,
    ContactPointType,
    CustomerContactPoint,
    Household,
    Person,
)


def get_or_create_contact_point(
    *,
    point_type: str,
    value_hash: str,
    value_suffix: str | None = None,
    display_value: str = "",
) -> ContactPoint:
    """Dedup a signal to a single global node by (point_type, value_hash)."""
    contact_point, _created = ContactPoint.objects.get_or_create(
        point_type=point_type,
        value_hash=value_hash,
        defaults={
            "value_suffix": value_suffix,
            "display_value": display_value,
        },
    )
    return contact_point


def upsert_customer_contact_point(
    *,
    customer_profile_id: str,
    contact_point: ContactPoint,
    when=None,
) -> CustomerContactPoint:
    """
    Attach a ContactPoint to a shop's CustomerProfile, bumping usage counters.
    Idempotent per (customer_profile, contact_point): re-running increments
    use_count and advances last_used_at without creating a second row.
    """
    when = when or timezone.now()
    link, created = CustomerContactPoint.objects.get_or_create(
        customer_profile_id=customer_profile_id,
        contact_point=contact_point,
        defaults={
            "use_count": 1,
            "first_used_at": when,
            "last_used_at": when,
        },
    )
    if not created:
        CustomerContactPoint.objects.filter(pk=link.pk).update(
            use_count=F("use_count") + 1,
            last_used_at=when,
            updated_at=when,
        )
        link.refresh_from_db()
    return link


def _confidence_for_occurrences(occurrence_count: int) -> float:
    """
    Monotonic, bounded confidence from co-occurrence count. One shared order is
    weak (0.5); each additional co-occurrence adds 0.1 up to a 1.0 ceiling.
    Kept intentionally simple for v1 — a decay/reweighting sweep is deferred.
    """
    return min(1.0, 0.5 + 0.1 * max(0, occurrence_count - 1))


def upsert_contact_point_link(
    *,
    cp_a: ContactPoint,
    cp_b: ContactPoint,
    link_reason: str = ContactPointLinkReason.CO_OCCURRED_IN_ORDER,
    when=None,
) -> ContactPointLink | None:
    """
    Create-or-strengthen an UNDIRECTED edge between two ContactPoints.

    The pair is canonically ordered (smaller UUID as source) before writing, so
    A↔B and B↔A collapse to one row (matching the DB check + unique
    constraints). Returns None for a self-link (cp_a is cp_b).
    """
    if cp_a.id == cp_b.id:
        return None

    when = when or timezone.now()
    # Canonical ordering: source_id < target_id.
    source, target = (cp_a, cp_b) if cp_a.id < cp_b.id else (cp_b, cp_a)

    link, created = ContactPointLink.objects.get_or_create(
        source=source,
        target=target,
        link_reason=link_reason,
        defaults={
            "occurrence_count": 1,
            "confidence_score": _confidence_for_occurrences(1),
            "first_seen_at": when,
            "last_seen_at": when,
        },
    )
    if not created:
        new_count = link.occurrence_count + 1
        ContactPointLink.objects.filter(pk=link.pk).update(
            occurrence_count=new_count,
            confidence_score=_confidence_for_occurrences(new_count),
            last_seen_at=when,
            updated_at=when,
        )
        link.refresh_from_db()
    return link


# ─────────────────────────────────────────────────────────────────────────────
# Layer 3 — Person / Household resolution (evidence tiers, design doc §6).
#
# The functions below cluster ContactPoints/ChannelActors into Persons and
# Households. They follow the same rules as the pure writers above: keyed on
# stable identifiers, get_or_create / update_or_create for idempotency, and
# never hard-delete (merges are append-only via Person.merged_into) so a wrong
# merge stays auditable and reversible.
# ─────────────────────────────────────────────────────────────────────────────


def resolve_terminal_person(person: Person, *, _max_hops: int = 64) -> Person:
    """
    Follow ``Person.merged_into`` to the terminal (canonical) Person.

    Merges are append-only: the loser keeps a pointer at the winner rather than
    being deleted, so any Person handed to us may be a stale alias. We ALWAYS
    resolve to the terminal node before attributing new signals, otherwise a
    signal would land on a dead alias and silently detach from the real cluster.

    Guards against cycles (a corrupt A→B→A chain) with a bounded hop count so a
    bad merge can never spin the resolver into an infinite loop; on hitting the
    bound we return the last node reached rather than raising, keeping the async
    task resilient.
    """
    seen: set = {person.id}
    current = person
    hops = 0
    while current.merged_into_id is not None and hops < _max_hops:
        nxt = current.merged_into
        if nxt is None or nxt.id in seen:
            # SET_NULL race or a cycle — stop at the last good node.
            break
        seen.add(nxt.id)
        current = nxt
        hops += 1
    return current


def get_or_create_person_for_contact_point(
    contact_point: ContactPoint,
    *,
    display_name: str = "",
    is_verified: bool = False,
    when=None,
) -> Person:
    """
    Attribute a ContactPoint to a Person, returning the Person.

    If the ContactPoint already belongs to a Person we return that Person's
    TERMINAL node (following any merges) — a signal is deduped globally to one
    ContactPoint, so its owning Person is authoritative and we must not mint a
    second one. Otherwise we create a fresh Person and claim the signal.

    When the ContactPoint is a PHONE we set ``primary_phone_cp`` on a brand-new
    Person, since the phone is the strongest cheap anchor we have for display /
    preload. ``is_verified`` / ``display_name`` are propagated upward (verified
    is sticky — never downgraded; display_name only fills a blank).
    """
    when = when or timezone.now()

    if contact_point.person_id is not None:
        person = resolve_terminal_person(contact_point.person)
        _apply_person_attributes(
            person, display_name=display_name, is_verified=is_verified, when=when
        )
        return person

    person = Person.objects.create(
        display_name=display_name,
        is_verified=is_verified,
        primary_phone_cp=(
            contact_point if contact_point.point_type == ContactPointType.PHONE else None
        ),
    )
    ContactPoint.objects.filter(pk=contact_point.pk).update(person=person, updated_at=when)
    contact_point.person = person
    return person


def _apply_person_attributes(
    person: Person, *, display_name: str = "", is_verified: bool = False, when=None
) -> None:
    """
    Propagate best-known attributes onto a Person without ever downgrading.

    ``is_verified`` is sticky (True once any strong signal is seen), and
    ``display_name`` only fills a blank so a real name captured once is not
    clobbered by a later empty order. No-op when nothing changes so we do not
    churn ``updated_at`` on every resolve.
    """
    when = when or timezone.now()
    fields: list[str] = []
    if is_verified and not person.is_verified:
        person.is_verified = True
        fields.append("is_verified")
    if display_name and not person.display_name:
        person.display_name = display_name
        fields.append("display_name")
    if fields:
        person.updated_at = when
        person.save(update_fields=[*fields, "updated_at"])


def merge_persons(person_a: Person, person_b: Person, *, when=None) -> Person:
    """
    STRONG-evidence merge: collapse two Persons proven to be the same human into
    one, append-only.

    WINNER/LOSER RULE: the OLDER Person (smaller ``created_at``; ``id`` as a
    stable tiebreaker) wins, so the canonical node is the earliest-known
    identity and merges are deterministic regardless of call-argument order.
    Both are resolved to their terminals first, so merging two aliases collapses
    their real clusters rather than re-pointing dead nodes.

    The loser's ``merged_into`` is set to the winner (never hard-deleted), and
    its ContactPoints and ChannelActors are re-parented to the winner so the
    winner becomes the single owner of every signal. ``is_verified`` and the
    best ``display_name`` propagate up; the winner adopts a ``primary_phone_cp``
    if it lacks one. Returns the winning (terminal) Person. Self-merge is a
    no-op.
    """
    when = when or timezone.now()
    winner = resolve_terminal_person(person_a)
    loser = resolve_terminal_person(person_b)

    if winner.id == loser.id:
        return winner

    # Oldest wins; id breaks ties so the choice is stable and order-independent.
    if (loser.created_at, str(loser.id)) < (winner.created_at, str(winner.id)):
        winner, loser = loser, winner

    # Re-parent the loser's signals to the winner.
    ContactPoint.objects.filter(person=loser).update(person=winner, updated_at=when)
    ChannelActor.objects.filter(person=loser).update(person=winner, updated_at=when)

    # Propagate best-known attributes onto the winner.
    _apply_person_attributes(
        winner, display_name=loser.display_name, is_verified=loser.is_verified, when=when
    )

    winner_fields: list[str] = []
    if winner.primary_phone_cp_id is None and loser.primary_phone_cp_id is not None:
        winner.primary_phone_cp_id = loser.primary_phone_cp_id
        winner_fields.append("primary_phone_cp")
    # If the winner has no household but the loser does, inherit it so the weak
    # cluster is not orphaned by the merge.
    if winner.household_id is None and loser.household_id is not None:
        winner.household_id = loser.household_id
        winner_fields.append("household")
    if winner_fields:
        winner.updated_at = when
        winner.save(update_fields=[*winner_fields, "updated_at"])

    # Fold the loser's behavioral counts into the winner before tombstoning, so
    # the aggregate stays consistent with the append-only merge. Local import:
    # behavioral imports from identity.models, and this keeps the merge path free
    # of an import cycle.
    from identity.services.behavioral import merge_aggregates

    merge_aggregates(winner_person_id=str(winner.id), loser_person_id=str(loser.id), when=when)

    # Append-only tombstone: point the loser at the winner, keep the row.
    loser.merged_into = winner
    loser.updated_at = when
    loser.save(update_fields=["merged_into", "updated_at"])

    return winner


def link_persons_into_household(
    person_a: Person, person_b: Person, *, label: str = "", when=None
) -> Household | None:
    """
    WEAK-evidence link: put two DISTINCT Persons in the same Household WITHOUT
    merging — the "family shares one phone / one address" case.

    Both Persons are resolved to their terminals first. If neither has a
    Household we create one; if exactly one has it the other joins; if both
    already have DIFFERENT Households we fold the younger household's members
    into the older one (older wins, matching the merge rule) so a shared signal
    never leaves two rival clusters. Returns the surviving Household, or None for
    a self-link (already the same Person).
    """
    when = when or timezone.now()
    a = resolve_terminal_person(person_a)
    b = resolve_terminal_person(person_b)

    if a.id == b.id:
        return None

    ha, hb = a.household, b.household

    if ha is None and hb is None:
        household = Household.objects.create(label=label)
        Person.objects.filter(pk__in=[a.pk, b.pk]).update(household=household, updated_at=when)
        return household

    if ha is not None and hb is None:
        Person.objects.filter(pk=b.pk).update(household=ha, updated_at=when)
        return ha

    if hb is not None and ha is None:
        Person.objects.filter(pk=a.pk).update(household=hb, updated_at=when)
        return hb

    # Both already have a household.
    if ha.id == hb.id:
        return ha

    # Distinct households: older survives, absorb the younger's members.
    survivor, absorbed = (
        (ha, hb) if (ha.created_at, str(ha.id)) <= (hb.created_at, str(hb.id)) else (hb, ha)
    )
    Person.objects.filter(household=absorbed).update(household=survivor, updated_at=when)
    return survivor


# ─────────────────────────────────────────────────────────────────────────────
# Order → Person attribution (the online orchestrator called by the async task).
# ─────────────────────────────────────────────────────────────────────────────

# Order.verification_method values (orders.models.VerificationMethod). Compared
# as string literals so identity does not import orders at module load (orders
# already imports identity — a module-level back-import would risk a cycle).
_STRONG_VERIFICATION_METHODS = frozenset({"OTP", "SOCIAL"})


def _order_has_strong_ownership(order) -> bool:
    """
    Does this order carry STRONG proof of phone ownership (design doc §6)?

    True for a storefront login (an authenticated ``user``), an OTP-verified
    order, or a SOCIAL auto-verification ("explicit this-is-me"). A CALL
    verification is deliberately NOT strong — a voice call confirms the order,
    not that the caller owns the number, which is exactly the family / shared
    phone case that must stay WEAK (Household, not merge).
    """
    if getattr(order, "user_id", None) is not None:
        return True
    return bool(order.is_verified) and order.verification_method in _STRONG_VERIFICATION_METHODS


def _names_match(name_a: str, name_b: str) -> bool:
    """Case/space-insensitive equality of two display names; blanks never match
    (an unknown name is not evidence of sameness)."""
    a = (name_a or "").strip().lower()
    b = (name_b or "").strip().lower()
    return bool(a) and a == b


def _claim_signals(person: Person, contact_points, *, when=None) -> None:
    """
    Give ``person`` ownership of any UNOWNED ContactPoints in the list.

    A signal already owned by someone else is left alone — that is the shared
    phone in a Household, whose split lives at the Person layer, not on the
    ContactPoint (models.ContactPoint.person). This is what keeps attribution
    idempotent: on a re-run the CPs are already claimed, so nothing changes.
    """
    when = when or timezone.now()
    for contact_point in contact_points:
        if contact_point is None or contact_point.person_id is not None:
            continue
        ContactPoint.objects.filter(pk=contact_point.pk).update(person=person, updated_at=when)
        contact_point.person = person
        # Adopt a phone as the person's primary anchor if it lacks one.
        if contact_point.point_type == ContactPointType.PHONE and person.primary_phone_cp_id is None:
            person.primary_phone_cp = contact_point
            person.save(update_fields=["primary_phone_cp", "updated_at"])


def resolve_order_channel_actor(snapshot, person: Person | None = None, *, when=None):
    """
    Resolve the order's ChannelActor (via actors.get_or_create_channel_actor) and
    link it upward to ``person``.

    The actor seam is owned by services.actors; we only consume it and attach the
    Person. If the actor is already linked to a DIFFERENT terminal Person that is
    strong evidence (the same conversation endpoint is one human), so we merge the
    two and re-point the actor at the winner. Returns the actor, or None when the
    order has no channel (web checkout).
    """
    from identity.services import actors  # local: actors imports resolution.

    channel = snapshot.channel
    channel_identity = snapshot.channel_identity
    if not channel or not channel_identity:
        return None
    if channel not in ChannelActorChannel.values:
        return None

    when = when or timezone.now()
    actor = actors.get_or_create_channel_actor(
        shop_id=str(snapshot.order.shop_id),
        channel=channel,
        channel_identity=channel_identity,
        when=when,
    )

    if person is None:
        return actor

    if actor.person_id is None:
        ChannelActor.objects.filter(pk=actor.pk).update(person=person, updated_at=when)
        actor.person = person
    else:
        existing = resolve_terminal_person(actor.person)
        winner = resolve_terminal_person(person)
        if existing.id != winner.id:
            winner = merge_persons(existing, winner, when=when)
            ChannelActor.objects.filter(pk=actor.pk).update(person=winner, updated_at=when)
            actor.person = winner
    return actor


def attribute_snapshot_to_person(snapshot, *, when=None) -> Person | None:
    """
    Attribute an order's resolved signals to a Person, applying the evidence
    tiers (design doc §6). Called by resolve_order_identity_graph AFTER the
    snapshot's ContactPoint FKs are resolved, and re-used by the backfill.

    The PHONE is the anchor. The rules, in order:

    * No signals at all → nothing to attribute (None).
    * Phone not yet owned → this order mints/claims the phone's Person and its
      address / channel signals.
    * Phone already owned and the evidence is STRONG (this order proves ownership
      via OTP / login / social, OR it repeats the SAME name AND same address) →
      the SAME Person; confirm and enrich it. Two orders with identical
      verified phone+name+address therefore collapse to ONE Person.
    * Phone already owned but name/address DIFFER (weak, family voice case) → a
      DISTINCT Person that shares the phone, placed in the SAME Household as the
      phone owner. The distinct Person is anchored on its own address CP so a
      re-run finds it again instead of minting a duplicate.

    Idempotent: ownership is read off the ContactPoints, so re-running never
    creates a second Person or double-merges. Returns the attributed Person (the
    terminal node), or None when the order carried no usable identity signal.
    """
    when = when or timezone.now()

    phone_cp = snapshot.phone_contact_point
    address_cp = snapshot.address_contact_point
    channel_cp = snapshot.channel_contact_point
    name = (snapshot.display_name or "").strip()
    strong = _order_has_strong_ownership(snapshot.order)

    # No phone: attribute everything to a single Person anchored on the best
    # available signal (address, else channel). No household split without a
    # phone to share.
    if phone_cp is None:
        anchor = address_cp or channel_cp
        if anchor is None:
            return None
        person = get_or_create_person_for_contact_point(
            anchor, display_name=name, is_verified=strong, when=when
        )
        _claim_signals(person, [address_cp, channel_cp], when=when)
        resolve_order_channel_actor(snapshot, person, when=when)
        return person

    phone_owner = (
        resolve_terminal_person(phone_cp.person) if phone_cp.person_id is not None else None
    )

    # First identity ever seen on this phone → it owns the phone and its signals.
    if phone_owner is None:
        person = get_or_create_person_for_contact_point(
            phone_cp, display_name=name, is_verified=strong, when=when
        )
        _claim_signals(person, [address_cp, channel_cp], when=when)
        resolve_order_channel_actor(snapshot, person, when=when)
        return person

    # Phone already owned — decide STRONG (same Person) vs WEAK (Household).
    #
    # same_address requires POSITIVE confirmation: the order's address signal is
    # already owned by the phone owner. An unowned / absent / differently-owned
    # address is NOT "same" — over-merging is the irreversible mistake, so we only
    # collapse on evidence, never on the absence of it. This is why
    # same-phone + same-name + DIFFERENT-address stays weak (design doc §6).
    same_name = _names_match(name, phone_owner.display_name)
    same_address = (
        address_cp is not None
        and address_cp.person_id is not None
        and resolve_terminal_person(address_cp.person).id == phone_owner.id
    )
    strong_same = strong or (same_name and same_address)

    if strong_same:
        _apply_person_attributes(phone_owner, display_name=name, is_verified=strong, when=when)
        _claim_signals(phone_owner, [address_cp, channel_cp], when=when)
        resolve_order_channel_actor(snapshot, phone_owner, when=when)
        return phone_owner

    # WEAK: shared phone, differing identity → distinct Person, same Household.
    #
    # Anchor the distinct Person on a signal it can be RE-RESOLVED from on a
    # re-run (its own address, else its own channel) so the backfill stays
    # idempotent instead of minting a duplicate each pass. A signal already owned
    # by the phone owner is shared and cannot anchor the distinct person; a signal
    # owned by a third terminal Person means we already minted this person before
    # (a prior run) and must reuse it.
    weak_anchor = None
    for cp in (address_cp, channel_cp):
        if cp is None:
            continue
        if cp.person_id is None:
            weak_anchor = cp  # unowned → this person will claim it
            break
        if resolve_terminal_person(cp.person).id != phone_owner.id:
            weak_anchor = cp  # already owned by the distinct person (re-run)
            break

    if weak_anchor is not None:
        person = get_or_create_person_for_contact_point(
            weak_anchor, display_name=name, is_verified=strong, when=when
        )
    else:
        # Phone AND every other signal are shared with the owner and there is
        # nothing unique to anchor on. Mint a fresh distinct Person: this rare
        # fully-shared case is not perfectly re-resolvable, but minting is the
        # safe (reversible) choice — the wrong move would be to merge.
        person = Person.objects.create(display_name=name, is_verified=strong)
    _claim_signals(person, [address_cp, channel_cp], when=when)
    _apply_person_attributes(person, display_name=name, is_verified=strong, when=when)
    link_persons_into_household(phone_owner, person, when=when)
    resolve_order_channel_actor(snapshot, person, when=when)
    return person


# ─────────────────────────────────────────────────────────────────────────────
# Backfill — build Persons/Households from history (management-safe, idempotent).
# ─────────────────────────────────────────────────────────────────────────────


def backfill_persons_from_snapshots(*, when=None) -> dict:
    """
    Build Persons/Households from existing OrderIdentitySnapshot rows, replaying
    the same evidence tiers ``attribute_snapshot_to_person`` applies online.

    Use this to seed Layer 3 for orders that were resolved (ContactPoints exist)
    before Person resolution shipped, or to rebuild after a data fix. Only
    snapshots whose ContactPoints are already resolved are processed — this reads
    the graph the async task built; it does not re-resolve raw signals.

    IDEMPOTENT: attribution keys off ContactPoint.person ownership, so a snapshot
    whose phone (or sole anchor) is already owned takes the "already owned" path
    and mutates nothing. Re-running therefore neither creates duplicate Persons
    nor double-merges. Ordering is oldest-first (created_at, id) so the earliest
    order wins the phone and becomes the canonical owner deterministically.

    Runs each snapshot in its own transaction so one bad row cannot roll back the
    whole backfill. Returns counts for ops visibility.
    """
    from identity.models import OrderIdentitySnapshot

    when = when or timezone.now()
    processed = 0
    skipped = 0
    persons_before = Person.objects.count()

    snapshots = (
        OrderIdentitySnapshot.objects.filter(resolved_at__isnull=False)
        .select_related("order")
        .order_by("created_at", "id")
        .iterator()
    )
    for snapshot in snapshots:
        # Nothing the resolver could have attributed → skip (keeps counts honest).
        if (
            snapshot.phone_contact_point_id is None
            and snapshot.address_contact_point_id is None
            and snapshot.channel_contact_point_id is None
        ):
            skipped += 1
            continue
        with transaction.atomic():
            attribute_snapshot_to_person(snapshot, when=when)
        processed += 1

    return {
        "status": "ok",
        "processed": processed,
        "skipped": skipped,
        "persons_created": Person.objects.count() - persons_before,
    }
