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
    "salam", "assalamu alaikum", "salaam",
]

DEFAULT_WELCOME_RESPONSES: list[str] = [
    "আস্সালামু আলাইকুম! 😊 আমি কীভাবে সাহায্য করতে পারি?",
    "হ্যালো! আপনাকে স্বাগতম 🛍 — কী খুঁজছেন?",
    "Hi there! 👋 Welcome! How can I help you today?",
    "Hello! 😊 Feel free to ask about our products, pricing, or orders!",
    "Hey! Great to see you 🛍 — what can I help you with?",
]

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


def greeting_reply_text(responses: list[str] | None = None) -> str:
    """Pick a random welcome response from the configured list."""
    pool = responses if responses else DEFAULT_WELCOME_RESPONSES
    return random.choice(pool)
