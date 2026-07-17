"""
Canonical phone normalization and hashing — the SINGLE source of truth.

This module is a hashing KEYSTONE: its output must be byte-identical in every
environment and every app. `users.PhoneIdentity`, `fraud.GlobalFraudPool`,
`fraud.FraudReport`, and `identity.ContactPoint` all derive their phone_hash
from here so that the same physical number hash-joins EXACTLY across all of
them. Do not fork this logic, and do not make behavior depend on an optional
import — a differing hash silently breaks every cross-table identity join.

Canonical form is E.164 (e.g. '+8801712345678'), computed with the
`phonenumbers` library against a default region. All historical divergence
(national-form stripping in identity, raw-digits-with-country-code in
users/fraud) is replaced by this one representation.

`HASH_VERSION` is stamped alongside every hash so a future re-hash can be done
safely and incrementally. Bump it only if the canonical form below changes.
"""
from __future__ import annotations

import hashlib

import phonenumbers

# Bump ONLY when the canonical representation changes. Persisted next to each
# phone_hash so rows hashed under an older scheme can be identified and
# re-hashed without ambiguity.
HASH_VERSION = 1

# Default region used to parse numbers without an explicit country code.
# Bangladesh; national numbers like '01712345678' parse to '+8801712345678'.
DEFAULT_REGION = "BD"


def normalize_phone_e164(raw: str | None, *, default_region: str = DEFAULT_REGION) -> str:
    """
    Return the E.164 form of a phone number, or "" when it cannot be parsed.

    Accepts local ('01712345678'), international ('+8801712345678'), and
    loosely formatted ('+880 1712-345678') inputs — all collapse to the same
    canonical string. Unparseable input returns "" so callers get a stable,
    empty hash rather than a crash.
    """
    if not raw:
        return ""
    try:
        parsed = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException:
        return ""
    if not phonenumbers.is_valid_number(parsed):
        # Fall back to a possible-but-not-strictly-valid number so we still
        # produce a deterministic canonical form for edge-case numbering plans,
        # rather than silently dropping the signal.
        if not phonenumbers.is_possible_number(parsed):
            return ""
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def hash_phone(raw: str | None, *, default_region: str = DEFAULT_REGION) -> str:
    """
    sha256 of the E.164 canonical form. Returns "" for unparseable/empty input
    (NOT a hash of the empty string) so empty phones never collide on a shared
    sentinel hash.
    """
    canonical = normalize_phone_e164(raw, default_region=default_region)
    if not canonical:
        return ""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def phone_suffix(raw: str | None, *, default_region: str = DEFAULT_REGION) -> str:
    """
    Last 4 digits of the canonical national number, for suffix-assisted display
    and coarse matching. Derived from the E.164 form so it is stable regardless
    of input formatting.
    """
    canonical = normalize_phone_e164(raw, default_region=default_region)
    digits = "".join(filter(str.isdigit, canonical))
    return digits[-4:] if len(digits) >= 4 else digits


def mask_phone(raw: str | None, *, default_region: str = DEFAULT_REGION) -> str:
    """Non-PII display label, e.g. '*********2345'."""
    canonical = normalize_phone_e164(raw, default_region=default_region)
    digits = "".join(filter(str.isdigit, canonical))
    if not digits:
        return ""
    suffix = digits[-4:] if len(digits) >= 4 else digits
    return f"{'*' * max(0, len(digits) - len(suffix))}{suffix}"
