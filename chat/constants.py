"""
Chat app module-level constants.

RESPONSE_MODE is a hardcoded global override for the whole chat system:

  "BOT"    → the AI engine answers inbound messages automatically (default).
  "HUMAN"  → human-only mode. Inbound messages are still persisted and pushed to
             agents in real time, but the AI engine, greeting pre-filter,
             deterministic postbacks, and comment auto-reply are NOT invoked.
             Humans reply from the agent inbox.

This is a deliberate single toggle: changing it requires a code change + restart.
It layers on top of the per-conversation human takeover in
`chat.services.bot_state` (which silences the bot for one conversation at a time).
"""
from __future__ import annotations

RESPONSE_MODE = "BOT"


def bot_autoresponds() -> bool:
    """True when the bot should generate automatic replies."""
    return RESPONSE_MODE == "BOT"
