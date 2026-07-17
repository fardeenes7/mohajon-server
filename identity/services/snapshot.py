"""
Synchronous, order-time capture of identity signals into an
OrderIdentitySnapshot. Called from inside the checkout transaction. Does no
graph work and makes no external calls — it only records what was used so the
async resolver (identity.tasks.resolve_order_identity_graph) can rebuild the
graph later.
"""

from __future__ import annotations

from core.phone import HASH_VERSION
from identity.models import OrderIdentitySnapshot
from identity.services import hashing


def _compose_address(shipping_address: dict | None) -> tuple[str, str, str]:
    """
    Return (raw_address, address_hash, display_address) from a checkout
    shipping_address dict.

    NOTE (best-effort, tracked debt): the chat shipping_address dict uses
    address_line / district / division / thana, which does NOT map cleanly onto
    users.Address's street|city|postal. We map address_line→street,
    district→city, postal_code→'' so the ADDRESS hash is deterministic, but it
    is NOT guaranteed hash-exact with a users.Address row for the same physical
    address. Unifying the address schemas is deferred.
    """
    if not shipping_address:
        return "", "", ""

    street = shipping_address.get("address_line") or shipping_address.get("street") or ""
    city = (
        shipping_address.get("district")
        or shipping_address.get("city")
        or shipping_address.get("thana")
        or ""
    )
    postal = shipping_address.get("postal_code") or ""

    raw_address = hashing.normalize_address(street=street, city=city, postal_code=postal)
    address_hash = hashing.hash_address(street=street, city=city, postal_code=postal)

    display_parts = [
        p
        for p in (
            shipping_address.get("address_line") or shipping_address.get("street"),
            shipping_address.get("thana"),
            shipping_address.get("district"),
            shipping_address.get("division"),
        )
        if p
    ]
    display_address = ", ".join(display_parts)
    return raw_address, address_hash, display_address


def create_order_identity_snapshot(
    *,
    order,
    shipping_address: dict | None = None,
    channel: str | None = None,
    channel_identity: str | None = None,
    payment_method: str | None = None,
) -> OrderIdentitySnapshot:
    """Build the immutable snapshot row. Resolved FKs are left null; the async
    task fills them in post-commit."""
    raw_phone = ""
    phone_hash = ""
    display_phone = ""
    if shipping_address and shipping_address.get("phone"):
        phone = shipping_address["phone"]
        raw_phone = hashing.canonicalize_phone(phone)
        phone_hash = hashing.hash_phone(phone)
        display_phone = hashing.mask_phone(phone)

    raw_address, address_hash, display_address = _compose_address(shipping_address)
    display_name = (shipping_address or {}).get("name", "") or ""

    return OrderIdentitySnapshot.objects.create(
        order=order,
        raw_phone=raw_phone,
        phone_hash=phone_hash,
        # Stamp the current hash version when a phone is present so this row is
        # correctly identified as up-to-date during future re-hash operations.
        # Rows with no phone carry no phone hash and correctly stay at 0.
        hash_version=HASH_VERSION if phone_hash else 0,
        raw_address=raw_address,
        address_hash=address_hash,
        channel=channel or "",
        channel_identity=channel_identity or "",
        payment_method=payment_method or "",
        display_phone=display_phone,
        display_name=display_name,
        display_address=display_address,
    )
