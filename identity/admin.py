"""
Django Admin for the identity graph.

All model admins are READ-ONLY by default (has_add_permission,
has_change_permission, and has_delete_permission all return False). The global
identity graph must only be mutated through the service layer — raw admin saves
bypass the invariants enforced there (canonical ordering, append-only merges,
hash versioning, aggregate folding on merge).

Use these views for TRIAGE:
  - ContactPoint: inspect a phone/address/PSID hash, see who owns it.
  - Person: verify merge chains (merged_into), check is_verified and the primary
    phone anchor.
  - Household: see which Persons share a weak cluster.
  - ChannelActor: inspect per-shop PSIDs/waids and their Person resolution.
  - CustomerContactPoint: audit which profiles have been linked to a signal.
  - ContactPointLink: check co-occurrence edges and confidence scores.
  - OrderIdentitySnapshot: view per-order raw capture and resolution status.
  - PersonBehavioralAggregate: check cross-channel order fact counts.
"""

from django.contrib import admin

from identity.models import (
    ChannelActor,
    ContactPoint,
    ContactPointLink,
    CustomerContactPoint,
    Household,
    OrderIdentitySnapshot,
    Person,
    PersonBehavioralAggregate,
)


class _ReadOnlyAdmin(admin.ModelAdmin):
    """Base class that makes all identity admins read-only by default."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ─────────────────────────────────────────────────────────────────────────────
# ContactPoint
# ─────────────────────────────────────────────────────────────────────────────


@admin.register(ContactPoint)
class ContactPointAdmin(_ReadOnlyAdmin):
    list_display = (
        "id",
        "point_type",
        "display_value",
        "value_suffix",
        "hash_version",
        "person",
        "created_at",
    )
    list_filter = ("point_type", "hash_version")
    search_fields = ("value_hash", "value_suffix", "display_value")
    raw_id_fields = ("person",)
    readonly_fields = (
        "id",
        "point_type",
        "value_hash",
        "value_suffix",
        "display_value",
        "hash_version",
        "person",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)


# ─────────────────────────────────────────────────────────────────────────────
# Person
# ─────────────────────────────────────────────────────────────────────────────


@admin.register(Person)
class PersonAdmin(_ReadOnlyAdmin):
    list_display = (
        "id",
        "display_name",
        "is_verified",
        "household",
        "merged_into",
        "created_at",
    )
    list_filter = ("is_verified",)
    search_fields = ("id", "display_name")
    raw_id_fields = ("household", "merged_into", "primary_phone_cp")
    readonly_fields = (
        "id",
        "display_name",
        "is_verified",
        "household",
        "merged_into",
        "primary_phone_cp",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)


# ─────────────────────────────────────────────────────────────────────────────
# Household
# ─────────────────────────────────────────────────────────────────────────────


@admin.register(Household)
class HouseholdAdmin(_ReadOnlyAdmin):
    list_display = ("id", "label", "created_at")
    search_fields = ("id", "label")
    readonly_fields = ("id", "label", "created_at", "updated_at")
    ordering = ("-created_at",)


# ─────────────────────────────────────────────────────────────────────────────
# ChannelActor
# ─────────────────────────────────────────────────────────────────────────────


@admin.register(ChannelActor)
class ChannelActorAdmin(_ReadOnlyAdmin):
    list_display = (
        "id",
        "shop",
        "channel",
        "channel_identity",
        "person",
        "first_seen_at",
        "last_seen_at",
    )
    list_filter = ("channel",)
    search_fields = ("channel_identity", "id")
    raw_id_fields = ("shop", "person", "phone_cp")
    readonly_fields = (
        "id",
        "shop",
        "channel",
        "channel_identity",
        "person",
        "phone_cp",
        "first_seen_at",
        "last_seen_at",
        "deleted_at",
    )
    ordering = ("-last_seen_at",)


# ─────────────────────────────────────────────────────────────────────────────
# CustomerContactPoint
# ─────────────────────────────────────────────────────────────────────────────


@admin.register(CustomerContactPoint)
class CustomerContactPointAdmin(_ReadOnlyAdmin):
    list_display = (
        "id",
        "customer_profile",
        "contact_point",
        "is_primary",
        "use_count",
        "first_used_at",
        "last_used_at",
    )
    search_fields = ("customer_profile__id", "contact_point__id")
    raw_id_fields = ("customer_profile", "contact_point")
    readonly_fields = (
        "id",
        "customer_profile",
        "contact_point",
        "is_primary",
        "use_count",
        "first_used_at",
        "last_used_at",
        "created_at",
        "updated_at",
    )
    ordering = ("-last_used_at",)


# ─────────────────────────────────────────────────────────────────────────────
# ContactPointLink
# ─────────────────────────────────────────────────────────────────────────────


@admin.register(ContactPointLink)
class ContactPointLinkAdmin(_ReadOnlyAdmin):
    list_display = (
        "id",
        "source",
        "target",
        "link_reason",
        "occurrence_count",
        "confidence_score",
        "first_seen_at",
        "last_seen_at",
    )
    list_filter = ("link_reason",)
    search_fields = ("source__id", "target__id")
    raw_id_fields = ("source", "target")
    readonly_fields = (
        "id",
        "source",
        "target",
        "link_reason",
        "occurrence_count",
        "confidence_score",
        "first_seen_at",
        "last_seen_at",
        "created_at",
        "updated_at",
    )
    ordering = ("-last_seen_at",)


# ─────────────────────────────────────────────────────────────────────────────
# OrderIdentitySnapshot
# ─────────────────────────────────────────────────────────────────────────────


@admin.register(OrderIdentitySnapshot)
class OrderIdentitySnapshotAdmin(_ReadOnlyAdmin):
    list_display = (
        "id",
        "order",
        "display_phone",
        "display_name",
        "channel",
        "hash_version",
        "resolved_at",
        "created_at",
    )
    list_filter = ("channel", "hash_version")
    search_fields = ("order__id", "phone_hash", "display_phone", "display_name")
    raw_id_fields = (
        "order",
        "phone_contact_point",
        "address_contact_point",
        "channel_contact_point",
    )
    readonly_fields = (
        "id",
        "order",
        "raw_phone",
        "phone_hash",
        "hash_version",
        "raw_address",
        "address_hash",
        "channel",
        "channel_identity",
        "payment_method",
        "display_phone",
        "display_name",
        "display_address",
        "phone_contact_point",
        "address_contact_point",
        "channel_contact_point",
        "resolved_at",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)


# ─────────────────────────────────────────────────────────────────────────────
# PersonBehavioralAggregate
# ─────────────────────────────────────────────────────────────────────────────


@admin.register(PersonBehavioralAggregate)
class PersonBehavioralAggregateAdmin(_ReadOnlyAdmin):
    list_display = (
        "id",
        "person",
        "orders_placed",
        "orders_delivered",
        "orders_rto",
        "orders_cancelled",
        "courier_success_rate",
        "first_order_at",
        "last_order_at",
    )
    search_fields = ("person__id", "person__display_name")
    raw_id_fields = ("person",)
    readonly_fields = (
        "id",
        "person",
        "orders_placed",
        "orders_delivered",
        "orders_rto",
        "orders_cancelled",
        "courier_success_rate",
        "courier_rate_updated_at",
        "first_order_at",
        "last_order_at",
        "created_at",
        "updated_at",
    )
    ordering = ("-last_order_at",)
