"""
Greeting Pre-filter (EPIC B-02).

Before any AI invocation, check whether the inbound message is a simple
greeting.  On match: reply with a preset welcome and skip AI entirely —
saving credits and reducing latency.

Rules (from v0.6 Step 2):
  - Keyword list is configurable per shop via ShopSettings.messenger_greeting_keywords.
  - Matching is case-insensitive, punctuation-stripped, and FULL-MESSAGE only.
    A message like "hi I want to buy a shirt" does NOT match.
"""
from __future__ import annotations

import re
import random

DEFAULT_GREETING_KEYWORDS: list[str] = [
    "hi", "hello", "hey", "হ্যালো", "হাই", "ভাই", "আপু",
    "কেমন আছো", "how are you", "good morning", "good afternoon", "good evening",
    "salam", "assalamu alaikum", "salaam", "আসসালামু আলাইকুম", "আসসালামুয়ালাইকুম",
    "slm", "assalamualaikum", "asslamualaikum",
]

# Welcome replies for a plain greeting ("hi", "hello", "ভাই"). We ALWAYS open
# with a salam — per shop etiquette the bot greets with Assalamu alaikum.
DEFAULT_WELCOME_RESPONSES: list[str] = [
    "আসসালামু আলাইকুম! 😊 কীভাবে সাহায্য করতে পারি?",
    "আসসালামু আলাইকুম! আপনাকে স্বাগতম 🛍️ — কী খুঁজছেন বলুন তো?",
    "Assalamu alaikum! 👋 Welcome — how can I help you today?",
    "Assalamu alaikum! 😊 Feel free to ask about our products, pricing, or orders!",
]

# Replies when the CUSTOMER greeted with a salam first — we must open with
# "Walaikum assalam" before welcoming them.
DEFAULT_SALAM_REPLY_RESPONSES: list[str] = [
    "ওয়ালাইকুম আসসালাম! 😊 কীভাবে সাহায্য করতে পারি?",
    "ওয়ালাইকুম আসসালাম! আপনাকে স্বাগতম 🛍️ — কী খুঁজছেন?",
    "Walaikum assalam! 👋 How can I help you today?",
]

# Salam keywords: if the inbound greeting is one of these, the customer greeted
# with a salam first, so we reply with "Walaikum assalam".
_SALAM_KEYWORDS: set[str] = {
    "salam", "salaam", "slm", "assalamu alaikum", "assalamualaikum",
    "asslamualaikum", "আসসালামু আলাইকুম", "আসসালামুয়ালাইকুম",
}

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    return _PUNCT_RE.sub("", text).strip().lower()


def is_greeting(
    *,
    message_text: str,
    keywords: list[str] | None = None,
) -> bool:
    """
    Return True if the entire message (after normalisation) matches one of
    the greeting keywords exactly.  Partial matches are NOT counted.
    """
    effective_keywords = [_normalize(k) for k in (keywords or DEFAULT_GREETING_KEYWORDS)]
    normalised = _normalize(message_text)
    return normalised in effective_keywords


def _is_salam(message_text: str) -> bool:
    """True if the customer's greeting is itself a salam."""
    return _normalize(message_text) in {_normalize(k) for k in _SALAM_KEYWORDS}


def greeting_reply_text(
    responses: list[str] | None = None,
    *,
    message_text: str | None = None,
) -> str:
    """
    Pick a welcome response.

    If the customer greeted with a salam first, reply with a "Walaikum assalam"
    variant; otherwise open with an "Assalamu alaikum" welcome. Passing an
    explicit ``responses`` list (shop-configured) overrides both.
    """
    if responses:
        return random.choice(responses)
    if message_text is not None and _is_salam(message_text):
        return random.choice(DEFAULT_SALAM_REPLY_RESPONSES)
    return random.choice(DEFAULT_WELCOME_RESPONSES)
