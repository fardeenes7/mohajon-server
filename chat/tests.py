import json
import uuid
import time
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from shops.models import Shop, SubscriptionPlan
import chat.services.engine
from chat.services.bot_state import (
    bot_state_set_human_active,
    bot_state_is_human_active,
    bot_state_clear_human_active,
    _key
)
from django_redis import get_redis_connection
import hmac
from hashlib import sha256


class WebhookHMACTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/v1/chat/webhook/"
        self.app_secret = "test_secret_12345"

    @override_settings(META_APP_SECRET="test_secret_12345")
    def test_webhook_valid_hmac(self):
        """A correctly-signed payload is accepted."""
        payload = json.dumps({"object": "page", "entry": []}).encode('utf-8')
        signature = hmac.new(self.app_secret.encode('utf-8'), msg=payload, digestmod=sha256).hexdigest()
        
        response = self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=f"sha256={signature}"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @override_settings(META_APP_SECRET="test_secret_12345")
    def test_webhook_invalid_hmac(self):
        """A payload with an invalid signature is rejected with 403."""
        payload = json.dumps({"object": "page", "entry": []}).encode('utf-8')
        invalid_signature = "bad_signature"
        
        response = self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=f"sha256={invalid_signature}"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.json(), {"detail": "Forbidden"})

    @override_settings(META_APP_SECRET="test_secret_12345")
    def test_webhook_missing_hmac(self):
        """A payload with a missing signature is rejected with 403."""
        payload = json.dumps({"object": "page", "entry": []}).encode('utf-8')
        
        response = self.client.post(
            self.url,
            data=payload,
            content_type="application/json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.json(), {"detail": "Forbidden"})


class BotStateTakeoverTestCase(TestCase):
    def setUp(self):
        self.page_id = "test_page_id"
        self.psid = "test_psid"
        self.r = get_redis_connection("default")
        self.r.flushall()

        plan = SubscriptionPlan.objects.create(name="FREE")
        self.shop = Shop.objects.create(id=uuid.uuid4(), name="Test Shop", subdomain="testshop", plan=plan)

    def tearDown(self):
        self.r.flushall()

    def test_human_takeover_state(self):
        """Triggering a takeover correctly flips the conversation into human handling state."""
        self.assertFalse(bot_state_is_human_active(page_id=self.page_id, psid=self.psid))
        
        bot_state_set_human_active(page_id=self.page_id, psid=self.psid, ttl_minutes=30)
        
        self.assertTrue(bot_state_is_human_active(page_id=self.page_id, psid=self.psid))

        # Check clearing
        bot_state_clear_human_active(page_id=self.page_id, psid=self.psid)
        self.assertFalse(bot_state_is_human_active(page_id=self.page_id, psid=self.psid))

    def test_human_takeover_expiry(self):
        """The state correctly clears/resets after timeout."""
        bot_state_set_human_active(page_id=self.page_id, psid=self.psid, ttl_minutes=-1) # Expired 1 min ago
        self.assertFalse(bot_state_is_human_active(page_id=self.page_id, psid=self.psid))

    @patch("chat.services.send_api.send_text")
    @patch("chat.tasks.process_inbound_message.retry")
    @patch("chat.services.engine.run_ai_turn")
    @patch("chat.services.greeting.is_greeting")
    def test_human_takeover_prevents_ai_processing(self, mock_is_greeting, mock_run_ai_turn, mock_retry, mock_send_text):
        """While in human handling state, inbound messages are NOT auto-processed by the AI engine."""
        from chat.tasks import process_inbound_message

        mock_is_greeting.return_value = False
        
        # Test bot processes when human is NOT active
        process_inbound_message.delay = lambda *args, **kwargs: process_inbound_message(*args, **kwargs)
        
        process_inbound_message(
            shop_id=str(self.shop.id),
            page_id=self.page_id,
            psid=self.psid,
            message_text="Hello bot",
            mid="mid.1",
            timestamp=12345,
            page_access_token="token"
        )
        mock_run_ai_turn.assert_called_once()
        mock_run_ai_turn.reset_mock()

        # Set human active
        bot_state_set_human_active(page_id=self.page_id, psid=self.psid)

        # Process should abort
        process_inbound_message(
            shop_id=str(self.shop.id),
            page_id=self.page_id,
            psid=self.psid,
            message_text="Hello human",
            mid="mid.2",
            timestamp=12345,
            page_access_token="token"
        )
        
        # Ensure AI engine was NOT called
        mock_run_ai_turn.assert_not_called()
