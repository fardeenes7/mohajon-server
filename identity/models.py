from __future__ import annotations

import uuid

from django.db import models


class ContactPointType(models.TextChoices):
    PHONE = "PHONE", "Phone"
    ADDRESS = "ADDRESS", "Address"
    FACEBOOK = "FACEBOOK", "Facebook"
    WHATSAPP = "WHATSAPP", "WhatsApp"
    EMAIL = "EMAIL", "Email"


class ContactPointLinkReason(models.TextChoices):
    CO_OCCURRED_IN_ORDER = "CO_OCCURRED_IN_ORDER", "Co-occurred in Order"


class ContactPoint(models.Model):
    """
    Global (non-tenant) identity node — one row per distinct identity signal
    (a phone, an address, a Facebook PSID, ...), deduped globally by
    (point_type, value_hash). Follows the GlobalFraudPool precedent
    (fraud/models/pool.py): a plain models.Model that bypasses RLS because it
    is not a TenantModel.

    PHONE HASHING (deliberate): value_hash for PHONE is computed from a
    *canonicalised* national form of the number (leading '880' country code /
    trunk '0' stripped — see services/hashing.canonicalize_phone). This is
    intentionally STRICTER than users.PhoneIdentity.phone_hash /
    GlobalFraudPool.phone_hash, which hash the raw digits-only string and keep
    the country code. Because the two hash spaces differ, joining a PHONE
    ContactPoint back to PhoneIdentity / GlobalFraudPool is SUFFIX-ASSISTED
    (value_suffix == last 4 digits == PhoneIdentity.phone_suffix), NOT
    hash-exact. Do NOT "unify" these hashes.

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
    raw_phone = models.CharField(max_length=32, blank=True)  # canonical national digits
    phone_hash = models.CharField(max_length=64, blank=True, db_index=True)
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
