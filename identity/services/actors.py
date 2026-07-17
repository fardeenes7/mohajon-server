"""
ChannelActor upsert — the Layer 2 seam between chat and the identity graph.

A ChannelActor is the durable identity of a conversation endpoint (a Messenger
PSID, a WhatsApp waid, a web-widget session) scoped to a shop. chat calls
`get_or_create_channel_actor` whenever it sees inbound/outbound traffic; the
identity graph owns everything past that call.

WHATSAPP IS SPECIAL: a WhatsApp `channel_identity` is the waid, which IS the
customer's phone number. So a WHATSAPP actor immediately mints a PHONE
ContactPoint (via the canonical core.phone hash) and links it as `phone_cp`.
This is the fix for the old bug where waid was stored as a Facebook PSID and
`channel` was hardcoded to FACEBOOK. Messenger PSIDs and web sessions carry no
phone signal, so `phone_cp` stays null for them.

Kept idempotent and pure (no external calls) so it is safe to call on every
message and safe to re-run.
"""

from __future__ import annotations

from django.utils import timezone

from core.phone import HASH_VERSION, hash_phone, mask_phone, phone_suffix
from identity.models import (
    ChannelActor,
    ChannelActorChannel,
    ContactPoint,
    ContactPointType,
)
from identity.services import resolution


def _mint_whatsapp_phone_cp(waid: str) -> ContactPoint | None:
    """
    Turn a WhatsApp waid into a canonical PHONE ContactPoint. Returns None if
    the waid does not canonicalize to a usable phone (so we never write an
    empty-hash node).
    """
    value_hash = hash_phone(waid)
    if not value_hash:
        return None
    return resolution.get_or_create_contact_point(
        point_type=ContactPointType.PHONE,
        value_hash=value_hash,
        value_suffix=phone_suffix(waid),
        display_value=mask_phone(waid),
        # waid is a phone number — stamp the current version so this PHONE
        # ContactPoint is identifiable as up-to-date in future re-hash sweeps.
        hash_version=HASH_VERSION,
    )


def get_or_create_channel_actor(
    *,
    shop_id: str,
    channel: str,
    channel_identity: str,
    when=None,
) -> ChannelActor:
    """
    Idempotent get-or-create of the ChannelActor for (shop, channel,
    channel_identity), advancing last_seen_at on every call.

    For WHATSAPP, mints/links the PHONE ContactPoint derived from the waid.
    Person resolution is NOT done here — that is the async resolver's job; this
    only guarantees the actor node (and, for WhatsApp, its phone signal) exists.
    """
    when = when or timezone.now()

    actor, created = ChannelActor.objects.get_or_create(
        shop_id=shop_id,
        channel=channel,
        channel_identity=channel_identity,
        defaults={
            "tenant_id": shop_id,
            "first_seen_at": when,
            "last_seen_at": when,
        },
    )

    update_fields: list[str] = []
    if not created:
        actor.last_seen_at = when
        update_fields.append("last_seen_at")

    # WhatsApp: ensure the phone signal exists and is linked.
    if channel == ChannelActorChannel.WHATSAPP and actor.phone_cp_id is None:
        phone_cp = _mint_whatsapp_phone_cp(channel_identity)
        if phone_cp is not None:
            actor.phone_cp = phone_cp
            update_fields.append("phone_cp")

    if update_fields:
        actor.save(update_fields=[*update_fields, "updated_at"])

    return actor
