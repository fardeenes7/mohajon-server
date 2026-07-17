"""
Hashing / canonicalisation for identity ContactPoints.

PHONE helpers are now THIN re-exports over core.phone — the single canonical
source of truth for phone normalization + hashing (E.164 based). This means a
PHONE ContactPoint.value_hash joins EXACTLY with users.PhoneIdentity.phone_hash
and fraud.GlobalFraudPool.phone_hash; the old national-form divergence is gone.

ADDRESS + channel_identity helpers are unchanged: they remain thin wrappers
over users.models.hash_text so ADDRESS hashes stay byte-compatible with
users.Address.address_hash. Those are NOT phone and must not move.
"""

from __future__ import annotations

from core.phone import hash_phone as _core_hash_phone
from core.phone import mask_phone as _core_mask_phone
from core.phone import normalize_phone_e164 as _core_normalize_phone_e164
from core.phone import phone_suffix as _core_phone_suffix
from users.models import hash_text


def canonicalize_phone(phone: str | None) -> str:
    """
    Canonical phone form (E.164, e.g. '+8801712345678'), delegated to
    core.phone.normalize_phone_e164. Returns "" for empty/unparseable input.

    Kept as a named wrapper so callers that persist the canonical string
    (snapshot.raw_phone) need no change. NOTE: this now returns the FULL E.164
    string, not the old national-core digits — the whole point of the unify.
    """
    return _core_normalize_phone_e164(phone)


def phone_suffix(phone: str | None) -> str:
    """Last 4 digits of the canonical number (core.phone.phone_suffix)."""
    return _core_phone_suffix(phone)


def hash_phone(phone: str | None) -> str:
    """value_hash for a PHONE ContactPoint — canonical E.164 sha256 via
    core.phone.hash_phone. Joins exactly with users/fraud phone_hash."""
    return _core_hash_phone(phone)


def normalize_address(*, street: str = "", city: str = "", postal_code: str = "") -> str:
    """
    Compose the canonical address string in the SAME shape as
    users.Address.save (street|city|postal, lowercased, outer-stripped) so
    ADDRESS hashes are join-compatible with users.Address.address_hash.
    """
    return f"{(street or '').lower().strip()}|{(city or '').lower().strip()}|{(postal_code or '').lower().strip()}"


def hash_address(*, street: str = "", city: str = "", postal_code: str = "") -> str:
    """value_hash for an ADDRESS ContactPoint."""
    return hash_text(normalize_address(street=street, city=city, postal_code=postal_code))


def hash_channel_identity(channel_identity: str | None) -> str:
    """
    value_hash for a FACEBOOK / WHATSAPP ContactPoint. The input is a
    page-scoped PSID (chat.Conversation.channel_identity) — a different value
    space from users.SocialAccount.provider_account_id. Only outer whitespace
    is stripped; the identifier is otherwise opaque.
    """
    return hash_text((channel_identity or "").strip())


def mask_phone(phone: str | None) -> str:
    """Non-PII display label, e.g. '*********2345' (core.phone.mask_phone)."""
    return _core_mask_phone(phone)
