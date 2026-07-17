from __future__ import annotations

import uuid

from django.db import models

from core.models import TenantModel


class ContactPointType(models.TextChoices):
    PHONE = "PHONE", "Phone"
    ADDRESS = "ADDRESS", "Address"
    FACEBOOK = "FACEBOOK", "Facebook"
    WHATSAPP = "WHATSAPP", "WhatsApp"
    EMAIL = "EMAIL", "Email"


class ContactPointLinkReason(models.TextChoices):
    CO_OCCURRED_IN_ORDER = "CO_OCCURRED_IN_ORDER", "Co-occurred in Order"


class ChannelActorChannel(models.TextChoices):
    FACEBOOK = "FACEBOOK", "Facebook Messenger"
    WHATSAPP = "WHATSAPP", "WhatsApp"
    WEB_WIDGET = "WEB_WIDGET", "Web Widget"


class Household(models.Model):
    """
    Layer 3 — WEAK identity cluster (global, non-tenant).

    Groups Persons who share a signal (a phone, an address) but are NOT proven
    to be the same human — the "family shares one phone number" case from the
    requirements. A Household never implies the members are one person; it only
    records that they are connected. Person merges are a separate, stronger
    operation (see Person.merged_into).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Ops-facing label only (e.g. a masked shared phone). Never authoritative.
    label = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Household({self.label or self.id})"


class Person(models.Model):
    """
    Layer 3 — a RESOLVED individual (global, non-tenant).

    A Person is the cross-channel identity that risk scoring, analytics, and
    address-preload hang off. It is a cluster of ContactPoints
    (ContactPoint.person) and ChannelActors (ChannelActor.person) that
    strong evidence says belong to one human.

    MERGES ARE APPEND-ONLY: when two Persons are proven to be the same human,
    the loser gets `merged_into` set to the winner rather than being deleted, so
    the graph stays auditable and a wrong merge is reversible. Always follow
    `merged_into` to the terminal Person before attributing new signals.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(
        Household,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="persons",
    )
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merged_from",
    )
    display_name = models.CharField(max_length=255, blank=True)  # best-known, non-authoritative
    # Denormalized convenience pointer to the Person's main phone signal.
    primary_phone_cp = models.ForeignKey(
        "identity.ContactPoint",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # True once any strong-evidence signal is present (OTP-verified phone,
    # storefront login, explicit self-identification).
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["merged_into"], name="person_merged_into_idx"),
            models.Index(fields=["household"], name="person_household_idx"),
        ]

    def __str__(self) -> str:
        return f"Person({self.display_name or self.id})"


class ChannelActor(TenantModel):
    """
    Layer 2 — the endpoint on the other side of a conversation (tenant-scoped).

    One row per (shop, channel, channel_identity): a Messenger PSID on a shop's
    page, a WhatsApp waid, or a web-widget session. Tenant-scoped because a PSID
    is only meaningful relative to a shop's page. Resolves UPWARD to a Person
    (nullable) via a shared ContactPoint — actors never link sideways to each
    other, which is why PSID↔PSID linkage is structurally impossible here.

    WhatsApp: `channel_identity` is the waid, which IS the phone number, so a
    WHATSAPP actor mints a PHONE ContactPoint (phone_cp) directly. Messenger
    PSIDs carry no such signal.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="channel_actors",
    )
    channel = models.CharField(max_length=20, choices=ChannelActorChannel.choices, db_index=True)
    channel_identity = models.CharField(max_length=255, db_index=True)
    person = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="actors",
    )
    # WhatsApp: the PHONE ContactPoint minted from waid. Null for Messenger/web.
    phone_cp = models.ForeignKey(
        "identity.ContactPoint",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    first_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            self.tenant_id = self.shop_id
        super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "channel", "channel_identity"],
                condition=models.Q(deleted_at__isnull=True),
                name="uq_channel_actor_shop_channel_identity",
            ),
        ]
        indexes = [
            models.Index(fields=["person"], name="channel_actor_person_idx"),
            models.Index(
                fields=["shop", "channel", "channel_identity"],
                name="channel_actor_lookup_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.channel}] {self.channel_identity}"


class ContactPoint(models.Model):
    """
    Global (non-tenant) identity node — one row per distinct identity signal
    (a phone, an address, a Facebook PSID, ...), deduped globally by
    (point_type, value_hash). Follows the GlobalFraudPool precedent
    (fraud/models/pool.py): a plain models.Model that bypasses RLS because it
    is not a TenantModel.

    PHONE HASHING: value_hash for PHONE now uses the shared canonical hash from
    core.phone (E.164 form, sha256 — see services/hashing.hash_phone). It is the
    SAME hash used by users.PhoneIdentity.phone_hash and
    fraud.GlobalFraudPool.phone_hash, so a PHONE ContactPoint joins EXACTLY
    (hash-to-hash) with those tables — no suffix assistance required. value_suffix
    is retained only as a coarse display / index aid.

    IDENTIFIER SPACES: a FACEBOOK ContactPoint hashes the page-scoped PSID
    (chat.Conversation.channel_identity), which is a DIFFERENT value space from
    users.SocialAccount.provider_account_id (an OAuth FB user id on the
    merchant side). These must never be collapsed together by hash.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    point_type = models.CharField(max_length=20, choices=ContactPointType.choices, db_index=True)
    value_hash = models.CharField(max_length=64, db_index=True)
    # Last 4 digits for PHONE (mirrors PhoneIdentity.phone_suffix); null/blank
    # for point types where a suffix is not meaningful.
    value_suffix = models.CharField(max_length=8, null=True, blank=True, db_index=True)
    display_value = models.CharField(max_length=255, blank=True)  # masked / non-PII label
    # Layer 3 resolution: the Person this signal has been attributed to (null
    # until the resolver clusters it). A signal shared across people (family
    # phone) stays on ONE ContactPoint; the split lives at the Person/Household
    # layer, not here.
    person = models.ForeignKey(
        "identity.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contact_points",
    )
    # core.phone.HASH_VERSION under which value_hash was computed (PHONE only);
    # 0 = legacy / pre-unify hash that has not been recomputed.
    hash_version = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["point_type", "value_hash"], name="uq_contact_point_type_hash"
            ),
        ]
        indexes = [
            models.Index(fields=["point_type", "value_hash"], name="cp_type_hash_idx"),
            models.Index(fields=["point_type", "value_suffix"], name="cp_type_suffix_idx"),
        ]

    def __str__(self) -> str:
        return f"[{self.point_type}] {self.display_value or self.value_hash[:8]}"


class CustomerContactPoint(models.Model):
    """
    Junction between a tenant-scoped shops.CustomerProfile and a global
    ContactPoint. Deliberately a plain models.Model (NOT a TenantModel): it
    references a tenant row by FK but must remain queryable across shops for
    the identity graph to be useful.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer_profile = models.ForeignKey(
        "shops.CustomerProfile",
        on_delete=models.CASCADE,
        related_name="contact_points",
    )
    contact_point = models.ForeignKey(
        ContactPoint,
        on_delete=models.CASCADE,
        related_name="customer_links",
    )
    is_primary = models.BooleanField(default=False)
    use_count = models.PositiveIntegerField(default=0)
    first_used_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["customer_profile", "contact_point"],
                name="uq_customer_contact_point",
            ),
        ]
        indexes = [
            models.Index(fields=["contact_point"], name="ccp_contact_point_idx"),
            models.Index(fields=["customer_profile"], name="ccp_customer_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.customer_profile_id} → {self.contact_point_id}"


class ContactPointLink(models.Model):
    """
    Undirected co-occurrence edge between two ContactPoints. Deliberately
    distinct from fraud.IdentityLink (which links User↔User); this links
    signal↔signal.

    CANONICAL ORDERING: always stored with source_id < target_id (enforced by
    services.resolution.upsert_contact_point_link and by the DB check
    constraint below) so that A↔B and B↔A collapse to a single row and
    occurrence_count is never double-counted. Uniqueness is on
    (source, target, link_reason).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(
        ContactPoint, on_delete=models.CASCADE, related_name="links_as_source"
    )
    target = models.ForeignKey(
        ContactPoint, on_delete=models.CASCADE, related_name="links_as_target"
    )
    link_reason = models.CharField(
        max_length=30,
        choices=ContactPointLinkReason.choices,
        default=ContactPointLinkReason.CO_OCCURRED_IN_ORDER,
    )
    occurrence_count = models.PositiveIntegerField(default=1)
    confidence_score = models.FloatField(default=0.0)
    first_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "target", "link_reason"],
                name="uq_contact_point_link",
            ),
            # Enforce the canonical undirected ordering at the DB level.
            models.CheckConstraint(
                condition=models.Q(source__lt=models.F("target")),
                name="ck_contact_point_link_canonical_order",
            ),
        ]
        indexes = [
            models.Index(fields=["source", "target"], name="cpl_source_target_idx"),
            models.Index(fields=["target"], name="cpl_target_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.source_id} ↔ {self.target_id} ({self.link_reason})"


class OrderIdentitySnapshot(models.Model):
    """
    Immutable, order-time capture of the identity signals used at checkout.
    Order's own identity FKs are live pointers (SET_NULL / PROTECT) that drift;
    this snapshot is the durable record. It is the ONLY structured identity
    persisted for chat orders (the chat path otherwise uses the shipping
    address phone for the risk check and then discards it).

    Written synchronously inside the checkout transaction. The resolved
    ContactPoint FKs and resolved_at are filled in asynchronously by
    tasks.resolve_order_identity_graph.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="identity_snapshot"
    )

    # ── Raw immutable capture (synchronous) ──────────────────────────────────
    raw_phone = models.CharField(max_length=32, blank=True)  # canonical E.164 phone
    phone_hash = models.CharField(max_length=64, blank=True, db_index=True)
    # core.phone.HASH_VERSION under which phone_hash was computed; 0 = legacy.
    hash_version = models.PositiveSmallIntegerField(default=0)
    raw_address = models.TextField(blank=True)  # composed street|city|postal
    address_hash = models.CharField(max_length=64, blank=True, db_index=True)
    channel = models.CharField(max_length=20, blank=True)  # FACEBOOK / WHATSAPP / '' (web)
    channel_identity = models.CharField(max_length=255, blank=True)  # page-scoped PSID
    payment_method = models.CharField(max_length=20, blank=True)

    # ── Denormalized display (synchronous, stays readable forever) ───────────
    display_phone = models.CharField(max_length=32, blank=True)  # masked
    display_name = models.CharField(max_length=255, blank=True)
    display_address = models.TextField(blank=True)

    # ── Resolved graph refs (filled asynchronously) ──────────────────────────
    phone_contact_point = models.ForeignKey(
        ContactPoint, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    address_contact_point = models.ForeignKey(
        ContactPoint, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    channel_contact_point = models.ForeignKey(
        ContactPoint, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"IdentitySnapshot(order={self.order_id})"


class PersonBehavioralAggregate(models.Model):
    """
    Neutral, per-Person behavioral FACTS (global, non-tenant) — the fact half of
    the identity/fraud split (design doc §7). Holds counts and an ingested
    courier delivery-success rate. It holds NO scores, thresholds, or risk
    levels: those are policy and live in `fraud`, which reads this aggregate
    through identity.services and never the other way around.

    Fed by explicit calls from orders on order-created / delivered / RTO /
    cancelled transitions (identity.services.behavioral), NOT by signals —
    matching the one-explicit-call-per-transition pattern the resolver uses.

    Keyed on the resolved Person so the counts are cross-channel by
    construction: a buyer's web, WhatsApp, and Messenger orders all roll up
    here. The Person is the terminal (post-merge) node; on a merge the loser's
    counts are folded into the winner's aggregate.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.OneToOneField(
        "identity.Person",
        on_delete=models.CASCADE,
        related_name="behavioral_aggregate",
    )

    orders_placed = models.PositiveIntegerField(default=0)
    orders_delivered = models.PositiveIntegerField(default=0)
    orders_rto = models.PositiveIntegerField(default=0)
    orders_cancelled = models.PositiveIntegerField(default=0)

    # Ingested from courier feeds (0.0–1.0), null until a feed is seen. This is
    # a reported observation about the phone, not a judgment we computed.
    courier_success_rate = models.FloatField(null=True, blank=True)
    courier_rate_updated_at = models.DateTimeField(null=True, blank=True)

    first_order_at = models.DateTimeField(null=True, blank=True)
    last_order_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["person"], name="pba_person_idx"),
        ]

    def __str__(self) -> str:
        return (
            f"Aggregate(person={self.person_id}, placed={self.orders_placed}, "
            f"delivered={self.orders_delivered}, rto={self.orders_rto})"
        )
