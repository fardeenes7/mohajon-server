"""
Meta Send API client re-exporter.

Re-exports all Meta Send API helpers from chat.services.send_api to maintain
a single canonical implementation and eliminate code duplication.
"""
from chat.services.send_api import (
    configure_messenger_profile,
    fetch_user_profile_data,
    fetch_user_profile_name,
    reply_to_comment,
    send_button_template,
    send_generic_template,
    send_quick_replies,
    send_receipt_template,
    send_text,
    send_typing_off,
    send_typing_on,
    subscribe_page_to_webhooks,
)

__all__ = [
    "configure_messenger_profile",
    "fetch_user_profile_data",
    "fetch_user_profile_name",
    "reply_to_comment",
    "send_button_template",
    "send_generic_template",
    "send_quick_replies",
    "send_receipt_template",
    "send_text",
    "send_typing_off",
    "send_typing_on",
    "subscribe_page_to_webhooks",
]
