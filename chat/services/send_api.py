"""
Meta Send API client.

Provides helpers for:
  - Sending text messages
  - Sending Messenger templates (Generic, Quick Reply, Button, Receipt)
  - Sender actions (typing_on / typing_off) with human-like pacing
  - Configuring Messenger Profile (Welcome Screen, Ice Breakers, Persistent Menu)

Graph API version: v21.0
Endpoint: https://graph.facebook.com/v21.0/me/messages
"""
from __future__ import annotations

import math
import time
from typing import Any

import requests
from django.conf import settings

_GRAPH_API_VERSION = "v21.0"
_MESSAGES_URL = f"https://graph.facebook.com/{_GRAPH_API_VERSION}/me/messages"
_PROFILE_URL = f"https://graph.facebook.com/{_GRAPH_API_VERSION}/me/messenger_profile"

# Typing pacing constants (v0.6 Step 5)
_MIN_DELAY_MS = 800
_MAX_DELAY_MS = 3000
_MS_PER_CHAR = 50


def _post(url: str, *, token: str, payload: dict) -> dict:
    resp = requests.post(url, params={"access_token": token}, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Sender Actions
# ---------------------------------------------------------------------------

def _typing_delay(text: str) -> float:
    """Return seconds to sleep proportional to message length."""
    chars = len(text)
    ms = min(_MIN_DELAY_MS + chars * _MS_PER_CHAR, _MAX_DELAY_MS)
    return ms / 1000.0


def send_typing_on(*, psid: str, token: str) -> None:
    _post(_MESSAGES_URL, token=token, payload={
        "recipient": {"id": psid},
        "sender_action": "typing_on",
    })


def send_typing_off(*, psid: str, token: str) -> None:
    _post(_MESSAGES_URL, token=token, payload={
        "recipient": {"id": psid},
        "sender_action": "typing_off",
    })


def send_text(*, psid: str, text: str, token: str) -> dict:
    """Send a plain-text message with human-like typing simulation."""
    send_typing_on(psid=psid, token=token)
    time.sleep(_typing_delay(text))
    send_typing_off(psid=psid, token=token)
    return _post(_MESSAGES_URL, token=token, payload={
        "recipient": {"id": psid},
        "message": {"text": text},
    })


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def send_generic_template(*, psid: str, elements: list[dict], token: str) -> dict:
    """
    Generic Template carousel.  Each element dict should contain:
      title, subtitle (opt), image_url (opt), default_action (opt), buttons (opt).
    Max 10 elements per carousel.
    """
    send_typing_on(psid=psid, token=token)
    time.sleep(_MIN_DELAY_MS / 1000.0)
    send_typing_off(psid=psid, token=token)
    return _post(_MESSAGES_URL, token=token, payload={
        "recipient": {"id": psid},
        "message": {
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "generic",
                    "elements": elements[:10],
                },
            }
        },
    })


def send_quick_replies(*, psid: str, text: str, quick_replies: list[dict], token: str) -> dict:
    """
    Quick Reply message.  Each quick_reply dict: {content_type, title, payload}.
    """
    send_typing_on(psid=psid, token=token)
    time.sleep(_typing_delay(text))
    send_typing_off(psid=psid, token=token)
    return _post(_MESSAGES_URL, token=token, payload={
        "recipient": {"id": psid},
        "message": {
            "text": text,
            "quick_replies": quick_replies,
        },
    })


def send_button_template(*, psid: str, text: str, buttons: list[dict], token: str) -> dict:
    """Button Template — up to 3 buttons."""
    send_typing_on(psid=psid, token=token)
    time.sleep(_typing_delay(text))
    send_typing_off(psid=psid, token=token)
    return _post(_MESSAGES_URL, token=token, payload={
        "recipient": {"id": psid},
        "message": {
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "button",
                    "text": text,
                    "buttons": buttons[:3],
                },
            }
        },
    })


def send_receipt_template(
    *,
    psid: str,
    order_number: str,
    recipient_name: str,
    currency: str,
    payment_method: str,
    elements: list[dict],
    summary: dict,
    token: str,
    address: dict | None = None,
) -> dict:
    """Receipt Template for COD confirmation."""
    payload_data: dict[str, Any] = {
        "template_type": "receipt",
        "recipient_name": recipient_name,
        "order_number": order_number,
        "currency": currency,
        "payment_method": payment_method,
        "elements": elements,
        "summary": summary,
    }
    if address:
        payload_data["address"] = address

    send_typing_on(psid=psid, token=token)
    time.sleep(_MIN_DELAY_MS / 1000.0)
    send_typing_off(psid=psid, token=token)
    return _post(_MESSAGES_URL, token=token, payload={
        "recipient": {"id": psid},
        "message": {
            "attachment": {
                "type": "template",
                "payload": payload_data,
            }
        },
    })


def reply_to_comment(*, comment_id: str, message: str, token: str) -> dict:
    """Post a public reply to a Facebook post comment via Graph API."""
    url = f"https://graph.facebook.com/{_GRAPH_API_VERSION}/{comment_id}/comments"
    resp = requests.post(url, params={"access_token": token}, json={"message": message}, timeout=10)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Messenger Profile  (Welcome Screen, Ice Breakers, Persistent Menu)
# ---------------------------------------------------------------------------

def configure_messenger_profile(*, token: str, shop_url: str) -> dict:
    """
    Sets the Welcome Screen greeting, Ice Breakers, and Persistent Menu
    for the connected Facebook Page.  Called once per shop on social
    connect and whenever ShopSettings.messenger_* fields change.
    """
    payload = {
        "get_started": {"payload": "GET_STARTED"},
        "greeting": [
            {"locale": "default", "text": "Welcome! How can I help you today? 🛍"},
        ],
        "ice_breakers": [
            {"question": "🛍 Browse Products", "payload": "ICE_BROWSE"},
            {"question": "📦 Track My Order", "payload": "ICE_TRACK"},
            {"question": "💬 FAQ", "payload": "ICE_FAQ"},
            {"question": "📞 Talk to Support", "payload": "ICE_SUPPORT"},
        ],
        "persistent_menu": [
            {
                "locale": "default",
                "composer_input_disabled": False,
                "call_to_actions": [
                    {
                        "type": "web_url",
                        "title": "🛍 Shop Now",
                        "url": shop_url,
                        "webview_height_ratio": "full",
                    },
                    {
                        "type": "postback",
                        "title": "📦 Track Order",
                        "payload": "TRACK_ORDER",
                    },
                    {
                        "type": "postback",
                        "title": "📋 FAQ",
                        "payload": "FAQ_MENU",
                    },
                    {
                        "type": "postback",
                        "title": "👤 Human Support",
                        "payload": "HUMAN_SUPPORT",
                    },
                ],
            }
        ],
    }
    return _post(_PROFILE_URL, token=token, payload=payload)
