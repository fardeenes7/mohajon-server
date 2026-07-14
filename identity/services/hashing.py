"""
Hashing / canonicalisation for identity ContactPoints.

This module is a THIN wrapper over users.models.normalize_phone / hash_text.
It does NOT reimplement sha256 — it reuses the exact same primitive so that
address hashes stay byte-compatible with users.Address.address_hash, and so a
PHONE hash is derived from the same sha256 function (over a canonical form).

See the class docstring on identity.models.ContactPoint for why PHONE hashing
is deliberately stricter than users.PhoneIdentity.phone_hash.
"""

from __future__ import annotations

from users.models import hash_text, normalize_phone

# Country code / trunk prefixes to strip when reducing a Bangladeshi number to
# a canonical national form. '880' is the BD country code; a leading trunk '0'
# is the national prefix. After normalize_phone (digits only) a local number is
# '01XXXXXXXXX' and an international one is '8801XXXXXXXXX'; both must reduce to
# the same '1XXXXXXXXX' canonical core so they hash identically.
_BD_COUNTRY_CODE = "880"


def canonicalize_phone(phone: str | None) -> str:
    """
    Reduce a phone number to a canonical national core (digits only, country
    code and trunk '0' stripped).

    Examples (all → '1712345678'):
        '01712345678'      -> '1712345678'
        '+880 1712-345678' -> '1712345678'
        '8801712345678'    -> '1712345678'

    Falls back to the plain digits-only form when the number does not match the
    known BD shape, so non-BD inputs still hash deterministically.
    """
    digits = normalize_phone(phone)
    if not digits:
        return ""

    if digits.startswith(_BD_COUNTRY_CODE):
        digits = digits[len(_BD_COUNTRY_CODE):]
    # After removing the country code (or for a purely local number) drop a
    # single leading trunk '0'.
    if digits.startswith("0"):
        digits = digits[1:]
    return digits


def phone_suffix(phone: str | None) -> str:
    """Last 4 digits of the *raw* normalized number — mirrors
    users.PhoneIdentity.phone_suffix so suffix-assisted joins line up."""
    digits = normalize_phone(phone)
    return digits[-4:] if len(digits) >= 4 else digits


def hash_phone(phone: str | None) -> str:
    """value_hash for a PHONE ContactPoint (canonical national form)."""
    return hash_text(canonicalize_phone(phone))


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
    """Non-PII display label, e.g. '******5678'."""
    digits = normalize_phone(phone)
    if not digits:
        return ""
    suffix = digits[-4:] if len(digits) >= 4 else digits
    return f"{'*' * max(0, len(digits) - len(suffix))}{suffix}"
