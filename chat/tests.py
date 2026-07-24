import json
import uuid
import time
from unittest.mock import patch
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

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

        from shops import models as shops_models
        plan = shops_models.SubscriptionPlan.objects.create(name="FREE")
        self.shop = shops_models.Shop.objects.create(id=uuid.uuid4(), name="Test Shop", subdomain="testshop", plan=plan)

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

    @patch("chat.channels.facebook.FacebookAdapter.send_text")
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

class AgentSendViewTestCase(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient
        from shops import models as shops_models
        import uuid
        self.client = APIClient()
        plan = shops_models.SubscriptionPlan.objects.create(name="FREE")
        self.shop = shops_models.Shop.objects.create(id=uuid.uuid4(), name="Test Shop", subdomain="testshop", plan=plan)
        
        # We need a user to authenticate
        from users.models import User
        self.user = User.objects.create_user(email="test@test.com", password="password")
        self.client.force_authenticate(user=self.user)
        
        shops_models.ShopMember.objects.create(shop=self.shop, user=self.user, role="OWNER")
        
        # We also need a SocialConnection to provide page token
        from marketing import models as marketing_models
        marketing_models.SocialConnection.objects.create(
            shop=self.shop,
            tenant_id=self.shop.id,
            provider="FACEBOOK",
            page_id="test_page_id",
            access_token="test_token"
        )
        self.url = "/api/v1/chat/send/"

    @patch("chat.channels.facebook.FacebookAdapter.send_text")
    def test_agent_send_creates_message_and_conversation(self, mock_send_text):
        mock_send_text.return_value = {"message_id": "mid.test1234"}
        
        # Need to simulate TenantMiddleware which sets request.tenant_id
        # We can just override _get_shop_id or we can pass headers if Middleware is active.
        # Let's see if we can pass HTTP_X_TENANT_ID header.
        
        response = self.client.post(
            self.url,
            {"page_id": "test_page_id", "psid": "test_psid", "text": "Hello from agent!"},
            format="json",
            HTTP_X_TENANT_ID=str(self.shop.id)
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "sent"})
        
        from chat.models import Conversation, ChatMessage, MessageDirection
        # Verify Conversation was created
        conv = Conversation.objects.get(shop=self.shop, channel="FACEBOOK", channel_identity="test_psid")
        self.assertIsNotNone(conv)
        
        # Verify ChatMessage was created
        msg = ChatMessage.objects.get(conversation=conv)
        self.assertEqual(msg.direction, MessageDirection.OUTBOUND)
        self.assertEqual(msg.text, "Hello from agent!")
import json
import uuid
import time
from unittest.mock import patch
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from shops.models import Shop, SubscriptionPlan, ShopSettings
from fraud.models.config import FraudConfig
from chat.services.tools import (
    _get_available_payment_methods,
    _get_invoice_payment_link,
    _confirm_order,
    _get_order_details,
)
from chat.services.bot_state import bot_state_set_order_draft
from catalog.models import Product
from orders.models import Order, OrderStatus
from identity.models import Person, ChannelActor, OrderIdentitySnapshot
from django_redis import get_redis_connection
import hmac
from hashlib import sha256


class ChatToolsTestCase(TestCase):
    def setUp(self):
        self.r = get_redis_connection("default")
        self.r.flushall()
        
        plan = SubscriptionPlan.objects.create(name="FREE")
        self.shop = Shop.objects.create(id=uuid.uuid4(), name="Test Shop", subdomain="testshop", plan=plan)
        
        # Selectors use ShopSettings and FraudConfig
        self.shop_settings = ShopSettings.objects.create(
            shop=self.shop, 
            tenant_id=self.shop.id,
            mandatory_advance_fee_bdt=50
        )
        self.fraud_config = FraudConfig.objects.create(
            shop=self.shop,
            tenant_id=self.shop.id,
            block_high_risk=True
        )
        
        self.page_id = "test_page"
        self.psid = "test_psid"

    def tearDown(self):
        self.r.flushall()

    def test_get_available_payment_methods(self):
        res = _get_available_payment_methods(shop_id=str(self.shop.id))
        self.assertIn("COD", res.get("methods", []))
        self.assertEqual(res.get("advance_delivery_fee_bdt"), 50)

    @patch("orders.services.invoices.payment_invoice_create")
    def test_get_invoice_payment_link(self, mock_create_invoice):
        # Requires a draft order
        bot_state_set_order_draft(page_id=self.page_id, psid=self.psid, draft={"quantity": 1})
        
        order = Order.objects.create(
            shop=self.shop,
            tenant_id=self.shop.id,
            status=OrderStatus.AWAITING_PAYMENT,
            total_amount=100.0,
            currency="BDT"
        )
        
        class MockInvoice:
            token = "inv_123"
        mock_create_invoice.return_value = MockInvoice()
        
        res = _get_invoice_payment_link(
            shop_id=str(self.shop.id),
            page_id=self.page_id,
            psid=self.psid,
            order_id=str(order.id)
        )
        self.assertEqual(res.get("payment_url"), f"https://{self.shop.subdomain}.mohajon.store/pay/inv_123")

    @patch("fraud.services.risk.check_customer_risk")
    @patch("orders.services.checkout.checkout_create_order")
    @patch("orders.services.transitions.order_transition")
    def test_confirm_order(self, mock_transition, mock_create, mock_risk):
        # We must exercise real FraudConfig selector, but we can mock risk to trigger it
        mock_risk.return_value = {"is_high_risk": True}
        
        bot_state_set_order_draft(page_id=self.page_id, psid=self.psid, draft={
            "product_id": str(uuid.uuid4()),
            "quantity": 1
        })
        
        # Because block_high_risk=True in FraudConfig and is_high_risk=True, this should error
        res = _confirm_order(
            shop_id=str(self.shop.id),
            page_id=self.page_id,
            psid=self.psid,
            shipping_address={"name": "Test", "phone": "01700000000", "address_line": "Test"},
            payment_method="COD"
        )
        self.assertIn("flagged for high risk", res.get("error", ""))
        
        # Test success path
        mock_risk.return_value = {"is_high_risk": False}
        class MockOrder:
            id = uuid.uuid4()
            status = "CONFIRMED"
            total_amount = 100.0
        mock_create.return_value = MockOrder()
        
        res2 = _confirm_order(
            shop_id=str(self.shop.id),
            page_id=self.page_id,
            psid=self.psid,
            shipping_address={"name": "Test", "phone": "01700000000", "address_line": "Test"},
            payment_method="COD"
        )
        self.assertEqual(res2.get("status"), "CONFIRMED")
        self.assertEqual(res2.get("payment_method"), "COD")

    def test_get_order_details(self):
        # Real identity and order lookup.
        # Person is a global (non-tenant) model: use display_name, no tenant_id.
        from identity.models import ContactPoint, ContactPointType, OrderIdentitySnapshot
        from identity.services.hashing import hash_channel_identity

        person = Person.objects.create(display_name="Test Person")

        # Create a ContactPoint for the PSID and link it to the Person.
        psid_hash = hash_channel_identity(self.psid)
        psid_cp = ContactPoint.objects.create(
            point_type=ContactPointType.FACEBOOK,
            value_hash=psid_hash,
            display_value=self.psid,
            person=person,
        )

        ChannelActor.objects.create(
            shop=self.shop,
            tenant_id=self.shop.id,
            channel="FACEBOOK",
            channel_identity=self.psid,
            person=person,
        )

        # order_list_for_customer resolves orders via OrderIdentitySnapshot →
        # ContactPoint → Person. The Order.customer_profile_id FK points to
        # shops_customerprofile, NOT identity.Person — do not pass it here.
        order = Order.objects.create(
            shop=self.shop,
            tenant_id=self.shop.id,
            status=OrderStatus.CONFIRMED,
            total_amount=200.0,
            currency="BDT",
        )

        # Wire the snapshot so the selector can find this order via the Person.
        OrderIdentitySnapshot.objects.create(
            order=order,
            channel="FACEBOOK",
            channel_identity=self.psid,
            channel_contact_point=psid_cp,
        )

        res = _get_order_details(
            shop_id=str(self.shop.id),
            psid=self.psid,
            order_identifier="last",
        )

        self.assertEqual(res.get("id"), str(order.id))
        self.assertEqual(res.get("total_amount"), "200.00")


class ChatRAGSignalsTestCase(TestCase):
    def setUp(self):
        plan = SubscriptionPlan.objects.create(name="FREE")
        self.shop = Shop.objects.create(id=uuid.uuid4(), name="Test Shop", subdomain="testshop", plan=plan)

    @patch("chat.tasks.rag.embed_product_specs.delay")
    def test_product_updated_signal(self, mock_embed):
        # Saving a product should fire catalog's product_updated signal,
        # which should be caught by chat's signal receiver and call embed_product_specs.delay.
        # total_stock is a computed @property (sum of variant stocks), not a DB field —
        # pass only actual model fields to objects.create().
        product = Product.objects.create(
            shop=self.shop,
            tenant_id=self.shop.id,
            name="Test Product",
            base_price=10.0,
        )
        # Because emit_product_updated is wrapped in transaction.on_commit we call
        # the signal directly so it fires inside the TestCase's non-committed transaction.
        from catalog.signals import product_updated
        product_updated.send(sender=Product, product_id=str(product.id))

        mock_embed.assert_called_with(product_id=str(product.id))

    @patch("chat.tasks.rag.backfill_skipped_embeddings.delay")
    def test_subscription_upgraded_signal(self, mock_backfill):
        from billing.signals import subscription_upgraded
        subscription_upgraded.send(sender=Shop, shop_id=str(self.shop.id))
        mock_backfill.assert_called_with(shop_id=str(self.shop.id))

    def test_embed_product_specs_service(self):
        # Confirm update_product_embedding writes the vector + status to the DB.
        # total_stock is a computed @property — do not pass it to objects.create().
        product = Product.objects.create(
            shop=self.shop,
            tenant_id=self.shop.id,
            name="Test Product",
            base_price=10.0,
        )
        from catalog.services import update_product_embedding

        update_product_embedding(product_id=str(product.id), vector=[0.1]*3072, status="CREATED")
        product.refresh_from_db()
        self.assertEqual(product.vector_status, "CREATED")
        self.assertIsNotNone(product.embedding)


class MultiChannelChatTestCase(TestCase):
    """Channel routing, bot/human toggle, unread state, and per-channel history."""

    def setUp(self):
        self.r = get_redis_connection("default")
        self.r.flushall()
        plan = SubscriptionPlan.objects.create(name="FREE")
        self.shop = Shop.objects.create(id=uuid.uuid4(), name="Test Shop", subdomain="testshop", plan=plan)
        self.page_id = "page_1"
        self.psid = "user_1"

    def tearDown(self):
        self.r.flushall()

    def _run(self, channel="FACEBOOK", text="hi there", psid=None):
        from chat.tasks import process_inbound_message
        process_inbound_message(
            shop_id=str(self.shop.id),
            page_id=self.page_id,
            psid=psid or self.psid,
            message_text=text,
            mid="mid.x",
            timestamp=111,
            page_access_token="token",
            channel=channel,
        )

    @patch("chat.services.greeting.is_greeting", return_value=False)
    @patch("chat.channels.whatsapp.WhatsAppAdapter.send_text")
    @patch("chat.services.engine.run_ai_turn", return_value="reply text")
    def test_whatsapp_reply_routes_via_whatsapp_adapter(self, mock_ai, mock_wa_send, _greet):
        self._run(channel="WHATSAPP")
        mock_ai.assert_called_once()
        mock_wa_send.assert_called_once()
        # channel_identity threaded, not the page_id
        _, kwargs = mock_wa_send.call_args
        self.assertEqual(kwargs.get("channel_identity"), self.psid)

    @patch("chat.services.greeting.is_greeting", return_value=False)
    @patch("chat.channels.facebook.FacebookAdapter.send_text")
    @patch("chat.services.engine.run_ai_turn", return_value="reply text")
    def test_facebook_reply_routes_via_facebook_adapter(self, mock_ai, mock_fb_send, _greet):
        self._run(channel="FACEBOOK")
        mock_fb_send.assert_called_once()

    @patch("chat.constants.RESPONSE_MODE", "HUMAN")
    @patch("chat.channels.facebook.FacebookAdapter.send_text")
    @patch("chat.services.engine.run_ai_turn")
    def test_human_only_mode_persists_but_skips_ai(self, mock_ai, mock_send):
        from chat.models import ChatMessage, MessageDirection
        self._run(channel="FACEBOOK", text="I need help")
        mock_ai.assert_not_called()
        mock_send.assert_not_called()
        msg = ChatMessage.objects.get(shop=self.shop)
        self.assertEqual(msg.direction, MessageDirection.INBOUND)
        self.assertEqual(msg.text, "I need help")

    def test_inbound_persist_bumps_unread(self):
        from chat.services.engine import _persist_message
        from chat.models import Conversation, MessageDirection
        _persist_message(
            shop_id=str(self.shop.id), channel="FACEBOOK", channel_identity=self.psid,
            direction=MessageDirection.INBOUND, text="a", timestamp=1, page_id=self.page_id,
        )
        _persist_message(
            shop_id=str(self.shop.id), channel="FACEBOOK", channel_identity=self.psid,
            direction=MessageDirection.INBOUND, text="b", timestamp=2, page_id=self.page_id,
        )
        conv = Conversation.objects.get(shop=self.shop, channel="FACEBOOK", channel_identity=self.psid)
        self.assertTrue(conv.has_unread)
        self.assertEqual(conv.unread_count, 2)
        # Outbound does NOT bump
        _persist_message(
            shop_id=str(self.shop.id), channel="FACEBOOK", channel_identity=self.psid,
            direction=MessageDirection.OUTBOUND, text="reply", timestamp=3, page_id=self.page_id,
        )
        conv.refresh_from_db()
        self.assertEqual(conv.unread_count, 2)

    def test_mark_conversation_read_resets(self):
        from chat.services.engine import _persist_message
        from chat.selectors import mark_conversation_read
        from chat.models import Conversation, MessageDirection
        _persist_message(
            shop_id=str(self.shop.id), channel="FACEBOOK", channel_identity=self.psid,
            direction=MessageDirection.INBOUND, text="a", timestamp=1, page_id=self.page_id,
        )
        mark_conversation_read(shop_id=str(self.shop.id), channel="FACEBOOK", psid=self.psid)
        conv = Conversation.objects.get(shop=self.shop, channel="FACEBOOK", channel_identity=self.psid)
        self.assertFalse(conv.has_unread)
        self.assertEqual(conv.unread_count, 0)
        self.assertIsNotNone(conv.last_read_at)

    def test_history_does_not_collide_across_channels(self):
        from chat.services.engine import _persist_message
        from chat.selectors import message_list_for_psid
        from chat.models import MessageDirection
        shared_id = "same_identity"
        _persist_message(
            shop_id=str(self.shop.id), channel="FACEBOOK", channel_identity=shared_id,
            direction=MessageDirection.INBOUND, text="fb-msg", timestamp=1,
        )
        _persist_message(
            shop_id=str(self.shop.id), channel="WHATSAPP", channel_identity=shared_id,
            direction=MessageDirection.INBOUND, text="wa-msg", timestamp=2,
        )
        fb = message_list_for_psid(shop_id=str(self.shop.id), psid=shared_id, channel="FACEBOOK")
        wa = message_list_for_psid(shop_id=str(self.shop.id), psid=shared_id, channel="WHATSAPP")
        self.assertEqual([m["content"] for m in fb], ["fb-msg"])
        self.assertEqual([m["content"] for m in wa], ["wa-msg"])

    def test_conversation_serializer_exposes_real_unread_fields(self):
        from chat.services.engine import _persist_message
        from chat.selectors import conversation_list_for_shop
        from chat.api.serializers import ConversationListSerializer
        from chat.models import MessageDirection
        _persist_message(
            shop_id=str(self.shop.id), channel="FACEBOOK", channel_identity=self.psid,
            direction=MessageDirection.INBOUND, text="a", timestamp=1, page_id=self.page_id,
        )
        data = ConversationListSerializer(conversation_list_for_shop(shop_id=str(self.shop.id)), many=True).data
        self.assertEqual(data[0]["unread_count"], 1)
        self.assertTrue(data[0]["has_unread"])
        self.assertEqual(data[0]["page_id"], self.page_id)

    def test_whatsapp_config_accepts_long_token(self):
        from chat.models import WhatsAppConfig
        long_token = "x" * 600
        cfg = WhatsAppConfig.objects.create(
            shop=self.shop, tenant_id=self.shop.id,
            phone_number_id="pn1", waba_id="waba1", access_token=long_token,
        )
        cfg.refresh_from_db()
        self.assertEqual(len(cfg.access_token), 600)


class MessengerPageOnboardingTestCase(TestCase):
    """Connecting a Facebook page subscribes it to webhooks + configures profile."""

    def setUp(self):
        plan = SubscriptionPlan.objects.create(name="FREE")
        self.shop = Shop.objects.create(id=uuid.uuid4(), name="Onboard Shop", subdomain="onboardshop", plan=plan)

    @patch("chat.tasks.onboard_messenger_page.delay")
    def test_upsert_social_connection_triggers_onboarding(self, mock_delay):
        from marketing.services import upsert_social_connection
        # transaction.on_commit hooks only run when the atomic block commits
        with self.captureOnCommitCallbacks(execute=True):
            upsert_social_connection(
                shop_id=str(self.shop.id), provider="META",
                page_id="page_123", page_name="My Page",
                access_token="tok_abc", expires_in=3600,
            )
        mock_delay.assert_called_once_with(shop_id=str(self.shop.id), page_id="page_123")

    @patch("chat.services.send_api.configure_messenger_profile")
    @patch("chat.services.send_api.subscribe_page_to_webhooks")
    def test_onboard_task_subscribes_and_configures(self, mock_sub, mock_profile):
        from marketing import models as marketing_models
        marketing_models.SocialConnection.objects.create(
            shop=self.shop, tenant_id=self.shop.id, provider="META",
            page_id="page_123", access_token="tok_abc",
        )
        from chat.tasks import onboard_messenger_page
        onboard_messenger_page(shop_id=str(self.shop.id), page_id="page_123")

        mock_sub.assert_called_once()
        _, kwargs = mock_sub.call_args
        self.assertEqual(kwargs["page_id"], "page_123")
        self.assertEqual(kwargs["token"], "tok_abc")
        mock_profile.assert_called_once()
        _, pkwargs = mock_profile.call_args
        self.assertEqual(pkwargs["token"], "tok_abc")
        self.assertIn("onboardshop", pkwargs["shop_url"])

    @patch("chat.services.send_api.configure_messenger_profile")
    @patch("chat.services.send_api.subscribe_page_to_webhooks")
    def test_onboard_task_noop_without_connection(self, mock_sub, mock_profile):
        from chat.tasks import onboard_messenger_page
        onboard_messenger_page(shop_id=str(self.shop.id), page_id="missing_page")
        mock_sub.assert_not_called()
        mock_profile.assert_not_called()


class ChatWebSocketTestCase(TransactionTestCase):
    """WebSocket auth + realtime fan-out using an in-memory channel layer.

    TransactionTestCase (not TestCase): the consumer's membership lookup runs in
    a separate thread via database_sync_to_async and must see committed rows.
    """

    def setUp(self):
        plan = SubscriptionPlan.objects.create(name="FREE")
        self.shop = Shop.objects.create(id=uuid.uuid4(), name="WS Shop", subdomain="wsshop", plan=plan)
        from users.models import User
        from shops.models import ShopMember
        self.user = User.objects.create_user(email="ws@test.com", password="pw")
        ShopMember.objects.create(shop=self.shop, user=self.user, role="OWNER")
        self.outsider = User.objects.create_user(email="outsider@test.com", password="pw")

    def _communicator(self, user, tenant):
        from channels.testing import WebsocketCommunicator
        from chat.consumers import AgentInboxConsumer
        comm = WebsocketCommunicator(AgentInboxConsumer.as_asgi(), f"/ws/chat/?tenant={tenant}")
        comm.scope["user"] = user
        return comm

    @override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
    def test_member_connects_and_receives_event(self):
        import asyncio
        from chat.services.realtime import publish_chat_event

        async def scenario():
            comm = self._communicator(self.user, str(self.shop.id))
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            from asgiref.sync import sync_to_async
            await sync_to_async(publish_chat_event)(
                shop_id=str(self.shop.id), event_type="chat_message", payload={"text": "hi"},
            )
            event = await comm.receive_json_from(timeout=2)
            self.assertEqual(event["type"], "chat_message")
            self.assertEqual(event["payload"]["text"], "hi")
            await comm.disconnect()

        asyncio.run(scenario())

    @override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
    def test_anonymous_rejected(self):
        import asyncio
        from django.contrib.auth.models import AnonymousUser

        async def scenario():
            comm = self._communicator(AnonymousUser(), str(self.shop.id))
            connected, _ = await comm.connect()
            self.assertFalse(connected)

        asyncio.run(scenario())

    @override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
    def test_non_member_tenant_rejected(self):
        import asyncio

        async def scenario():
            comm = self._communicator(self.outsider, str(self.shop.id))
            connected, _ = await comm.connect()
            self.assertFalse(connected)

        asyncio.run(scenario())



