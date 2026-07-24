"""
Tests for message debouncing and credit reservation system.

Test cases:
  1. Three rapid messages → one run_ai_turn with concatenated text
  2. Message after debounce window → separate turn
  3. Two concurrent workers → no duplicate flush
  4. Credit floor → two concurrent conversations can't overspend
  5. Crashed turn → reservation released
  6. Redis unavailable → immediate fallback processing

Note: Tests that exercise process_inbound_message mock get_shop / get_shop_settings
to avoid requiring a DB connection — the debounce logic only needs Redis, and
the DB interactions (Shop lookup, greeting filter) are tested separately in
chat/tests.py.
"""
import json
import uuid
import time
from decimal import Decimal
from unittest.mock import patch, MagicMock, call

from django.test import TestCase, override_settings
from django_redis import get_redis_connection

from chat.tasks import (
    process_inbound_message,
    _flush_debounced_messages,
    _debounce_queue_key,
    _debounce_meta_key,
    _debounce_lock_key,
    _debounce_and_schedule_ai_turn,
    _process_single_message_immediately,
)
from ai.services.ai_credits import (
    _reservation_key,
    reserve_ai_credits,
    reconcile_ai_credits,
    release_ai_credits,
    InsufficientCreditsError,
    _get_total_reserved,
)


def _mock_shop(shop_id):
    """Create a mock Shop object for tests that skip DB creation."""
    mock = MagicMock()
    mock.id = shop_id
    mock.name = "Test Shop"
    mock.subdomain = "testshop"
    return mock


class DebounceMessageBatchingTestCase(TestCase):
    """Test 1: Three rapid messages within the debounce window produce exactly
    one run_ai_turn call with all three messages concatenated in order."""

    def setUp(self):
        self.r = get_redis_connection("default")
        self.r.flushall()
        self.shop_id = str(uuid.uuid4())
        self.page_id = "test_page"
        self.psid = "user_rapid"

    def tearDown(self):
        self.r.flushall()

    @patch("chat.channels.facebook.FacebookAdapter.send_text")
    @patch("chat.services.engine.run_ai_turn", return_value="batch reply")
    @patch("chat.services.greeting.is_greeting", return_value=False)
    @patch("chat.tasks._flush_debounced_messages.apply_async")
    @patch("shops.selectors.get_shop_settings", return_value=None)
    @patch("shops.selectors.get_shop")
    @patch("chat.services.bot_state.bot_state_is_human_active", return_value=False)
    def test_three_rapid_messages_batch_into_one_turn(
        self, mock_bot_state, mock_get_shop, mock_get_settings,
        mock_flush_async, mock_is_greeting, mock_run_ai_turn, mock_send_text
    ):
        """Simulate 3 rapid messages: all should queue, and one flush task should
        be scheduled. When the flush runs, run_ai_turn is called once with
        all 3 messages."""
        mock_get_shop.return_value = _mock_shop(self.shop_id)

        # Send 3 messages rapidly — each calls process_inbound_message
        for i, text in enumerate(["hey", "are you open today?", "also do you deliver?"]):
            process_inbound_message(
                shop_id=self.shop_id,
                page_id=self.page_id,
                psid=self.psid,
                message_text=text,
                mid=f"mid.{i}",
                timestamp=1000 + i,
                page_access_token="token",
                channel="FACEBOOK",
            )

        # Only ONE flush task should have been scheduled (SETNX lock)
        self.assertEqual(mock_flush_async.call_count, 1)

        # Verify all 3 messages are in the Redis queue
        queue_key = _debounce_queue_key(self.shop_id, self.psid)
        queued = self.r.lrange(queue_key, 0, -1)
        self.assertEqual(len(queued), 3)

        # Parse and verify order
        texts = [json.loads(q)["text"] for q in queued]
        self.assertEqual(texts, ["hey", "are you open today?", "also do you deliver?"])

        # run_ai_turn should NOT have been called yet (debounce pending)
        mock_run_ai_turn.assert_not_called()

        # Now simulate the flush firing
        _flush_debounced_messages(shop_id=self.shop_id, psid=self.psid)

        # run_ai_turn should be called exactly once with inbound_texts
        mock_run_ai_turn.assert_called_once()
        _, kwargs = mock_run_ai_turn.call_args
        self.assertEqual(kwargs["inbound_texts"], [
            ("hey", 1000),
            ("are you open today?", 1001),
            ("also do you deliver?", 1002),
        ])

        # send_text should be called once with the reply
        mock_send_text.assert_called_once()


class DebounceWindowExpiryTestCase(TestCase):
    """Test 2: A message arriving after the debounce window closes (previous
    turn already dispatched) starts a new, separate turn."""

    def setUp(self):
        self.r = get_redis_connection("default")
        self.r.flushall()
        self.shop_id = str(uuid.uuid4())
        self.page_id = "test_page"
        self.psid = "user_window"

    def tearDown(self):
        self.r.flushall()

    @patch("chat.channels.facebook.FacebookAdapter.send_text")
    @patch("chat.services.engine.run_ai_turn", return_value="reply")
    @patch("chat.services.greeting.is_greeting", return_value=False)
    @patch("chat.tasks._flush_debounced_messages.apply_async")
    @patch("shops.selectors.get_shop_settings", return_value=None)
    @patch("shops.selectors.get_shop")
    @patch("chat.services.bot_state.bot_state_is_human_active", return_value=False)
    def test_message_after_flush_starts_new_turn(
        self, mock_bot_state, mock_get_shop, mock_get_settings,
        mock_flush_async, mock_is_greeting, mock_run_ai_turn, mock_send_text
    ):
        mock_get_shop.return_value = _mock_shop(self.shop_id)

        # First message queues and schedules flush
        process_inbound_message(
            shop_id=self.shop_id, page_id=self.page_id, psid=self.psid,
            message_text="first", mid="mid.1", timestamp=1000,
            page_access_token="token", channel="FACEBOOK",
        )
        self.assertEqual(mock_flush_async.call_count, 1)

        # Simulate the flush running (consumes the queue)
        _flush_debounced_messages(shop_id=self.shop_id, psid=self.psid)
        mock_run_ai_turn.assert_called_once()
        first_call_kwargs = mock_run_ai_turn.call_args[1]
        self.assertEqual(first_call_kwargs["inbound_text"], "first")

        mock_run_ai_turn.reset_mock()
        mock_flush_async.reset_mock()

        # Second message arrives after flush completed — should start fresh
        process_inbound_message(
            shop_id=self.shop_id, page_id=self.page_id, psid=self.psid,
            message_text="second", mid="mid.2", timestamp=2000,
            page_access_token="token", channel="FACEBOOK",
        )

        # A new flush should be scheduled
        self.assertEqual(mock_flush_async.call_count, 1)

        # Flush the second message
        _flush_debounced_messages(shop_id=self.shop_id, psid=self.psid)
        self.assertEqual(mock_run_ai_turn.call_count, 2)
        second_call_kwargs = mock_run_ai_turn.call_args[1]
        self.assertEqual(second_call_kwargs["inbound_text"], "second")


class DebounceSlowTurnSerializationTestCase(TestCase):
    """Test 3: A message arriving during a slow AI turn (while lock is held)
    does not spawn a concurrent flush task. Instead, the active turn schedules
    a sequential flush for it when it finishes."""

    def setUp(self):
        self.r = get_redis_connection("default")
        self.r.flushall()
        self.shop_id = str(uuid.uuid4())
        self.page_id = "test_page"
        self.psid = "user_slow"

    def tearDown(self):
        self.r.flushall()

    @patch("chat.channels.facebook.FacebookAdapter.send_text")
    @patch("chat.services.engine.run_ai_turn")
    @patch("chat.services.greeting.is_greeting", return_value=False)
    @patch("chat.tasks._flush_debounced_messages.apply_async")
    @patch("shops.selectors.get_shop_settings", return_value=None)
    @patch("shops.selectors.get_shop")
    @patch("chat.services.bot_state.bot_state_is_human_active", return_value=False)
    def test_message_during_slow_turn_is_serialized(
        self, mock_bot_state, mock_get_shop, mock_get_settings,
        mock_flush_async, mock_is_greeting, mock_run_ai_turn, mock_send_text
    ):
        mock_get_shop.return_value = _mock_shop(self.shop_id)

        # We use a side_effect to simulate a second webhook arriving
        # *during* the execution of the first run_ai_turn.
        def slow_turn_side_effect(*args, **kwargs):
            # The first turn is executing. lock_key is held in Redis.
            # Simulate the customer sending a second message right now.
            process_inbound_message(
                shop_id=self.shop_id, page_id=self.page_id, psid=self.psid,
                message_text="second", mid="mid.2", timestamp=2000,
                page_access_token="token", channel="FACEBOOK",
            )
            return "first reply"

        mock_run_ai_turn.side_effect = slow_turn_side_effect

        # 1. First message arrives
        process_inbound_message(
            shop_id=self.shop_id, page_id=self.page_id, psid=self.psid,
            message_text="first", mid="mid.1", timestamp=1000,
            page_access_token="token", channel="FACEBOOK",
        )

        # Exactly one flush task is scheduled for the first message
        self.assertEqual(mock_flush_async.call_count, 1)
        mock_flush_async.reset_mock()

        # 2. Execute the flush task
        # This pops 'first' from the queue, then calls run_ai_turn, which
        # fires slow_turn_side_effect, simulating 'second' arriving.
        _flush_debounced_messages(shop_id=self.shop_id, psid=self.psid)

        # 3. Verifications

        # run_ai_turn was called EXACTLY ONCE for 'first' during this execution
        self.assertEqual(mock_run_ai_turn.call_count, 1)
        self.assertEqual(mock_run_ai_turn.call_args[1]["inbound_text"], "first")

        # Because 'second' arrived while the lock was held, it was queued, but
        # it did NOT spawn a concurrent apply_async from process_inbound_message.
        # Instead, the finally block of the FIRST turn saw pending > 0 and
        # scheduled EXACTLY ONE sequential flush task.
        self.assertEqual(mock_flush_async.call_count, 1)

        # 4. If we execute that sequential flush task, it processes 'second'
        mock_run_ai_turn.side_effect = None
        mock_run_ai_turn.return_value = "second reply"
        _flush_debounced_messages(shop_id=self.shop_id, psid=self.psid)

        self.assertEqual(mock_run_ai_turn.call_count, 2)
        self.assertEqual(mock_run_ai_turn.call_args[1]["inbound_text"], "second")


class DeduplicateFlushSchedulingTestCase(TestCase):
    """Test 3: Two concurrent Celery workers receiving messages for the same
    psid do not both schedule a duplicate flush."""

    def setUp(self):
        self.r = get_redis_connection("default")
        self.r.flushall()
        self.shop_id = str(uuid.uuid4())

    def tearDown(self):
        self.r.flushall()

    @patch("chat.tasks._flush_debounced_messages.apply_async")
    def test_setnx_prevents_duplicate_flush(self, mock_flush_async):
        """Simulate two workers calling _debounce_and_schedule_ai_turn for
        the same psid. Only one should schedule the flush task."""
        psid = "concurrent_user"
        settings_obj = None

        # First call acquires the lock and schedules
        _debounce_and_schedule_ai_turn(
            shop_id=self.shop_id, page_id="page", psid=psid,
            message_text="msg1", timestamp=1000,
            channel="FACEBOOK", settings_obj=settings_obj,
        )
        self.assertEqual(mock_flush_async.call_count, 1)

        # Second call (from a concurrent worker) — lock already held
        _debounce_and_schedule_ai_turn(
            shop_id=self.shop_id, page_id="page", psid=psid,
            message_text="msg2", timestamp=1001,
            channel="FACEBOOK", settings_obj=settings_obj,
        )
        # Still only ONE flush scheduled
        self.assertEqual(mock_flush_async.call_count, 1)

        # Both messages are in the queue though
        queue_key = _debounce_queue_key(self.shop_id, psid)
        queued = self.r.lrange(queue_key, 0, -1)
        self.assertEqual(len(queued), 2)


class CreditReservationRaceTestCase(TestCase):
    """Test 4: A tenant at exactly their credit floor cannot have two
    concurrent conversations both pass the reservation check and overspend."""

    def setUp(self):
        self.r = get_redis_connection("default")
        self.r.flushall()
        self.shop_id = str(uuid.uuid4())

    def tearDown(self):
        self.r.flushall()

    @patch("ai.services.ai_credits.available_credits")
    @patch("ai.services.ai_credits.estimate_turn_credits")
    def test_concurrent_reservations_blocked(self, mock_estimate, mock_available):
        """With 0.05 credits available and each turn estimated at 0.03,
        the first reservation succeeds but the second must fail."""
        mock_available.return_value = Decimal("0.05")
        mock_estimate.return_value = Decimal("0.03")

        # First reservation: 0.03 <= 0.05, succeeds
        reserved1 = reserve_ai_credits(shop_id=self.shop_id)
        self.assertEqual(reserved1, Decimal("0.03"))

        # Verify Redis shows 0.03 reserved
        self.assertEqual(_get_total_reserved(self.shop_id), Decimal("0.03"))

        # Second reservation: 0.03 + 0.03 = 0.06 > 0.05, must fail
        with self.assertRaises(InsufficientCreditsError):
            reserve_ai_credits(shop_id=self.shop_id)

        # Only 0.03 remains reserved (the second was rejected)
        self.assertEqual(_get_total_reserved(self.shop_id), Decimal("0.03"))


class CrashedTurnReleasesReservationTestCase(TestCase):
    """Test 5: A crashed/exception-raising turn still releases its reservation."""

    def setUp(self):
        self.r = get_redis_connection("default")
        self.r.flushall()
        self.shop_id = str(uuid.uuid4())

    def tearDown(self):
        self.r.flushall()

    @patch("ai.services.ai_credits.available_credits")
    @patch("ai.services.ai_credits.estimate_turn_credits")
    def test_release_after_crash(self, mock_estimate, mock_available):
        """Reserve credits, then release them (simulating a finally block).
        The Redis key should return to 0."""
        mock_available.return_value = Decimal("1.00")
        mock_estimate.return_value = Decimal("0.05")

        reserved = reserve_ai_credits(shop_id=self.shop_id)
        self.assertEqual(reserved, Decimal("0.05"))

        # Verify reservation is visible
        res_key = _reservation_key(self.shop_id)
        self.assertIsNotNone(self.r.get(res_key))
        self.assertEqual(_get_total_reserved(self.shop_id), Decimal("0.05"))

        # Simulate crash recovery: release in finally block
        release_ai_credits(shop_id=self.shop_id, reserved_credits=reserved)

        # Reservation should be cleared
        self.assertEqual(_get_total_reserved(self.shop_id), Decimal("0"))

    @patch("ai.services.ai_credits.available_credits")
    @patch("ai.services.ai_credits.estimate_turn_credits")
    def test_reconcile_after_success(self, mock_estimate, mock_available):
        """Reserve, then reconcile after successful turn. Counter returns to 0."""
        mock_available.return_value = Decimal("1.00")
        mock_estimate.return_value = Decimal("0.05")

        reserved = reserve_ai_credits(shop_id=self.shop_id)
        reconcile_ai_credits(shop_id=self.shop_id, reserved_credits=reserved)

        self.assertEqual(_get_total_reserved(self.shop_id), Decimal("0"))


class RedisUnavailableFallbackTestCase(TestCase):
    """Test 6: Redis unavailability during debounce falls back to immediate
    single-message processing without dropping the message."""

    def setUp(self):
        self.shop_id = str(uuid.uuid4())

    @patch("chat.channels.facebook.FacebookAdapter.send_text")
    @patch("chat.services.engine.run_ai_turn", return_value="fallback reply")
    @patch("chat.services.greeting.is_greeting", return_value=False)
    @patch("chat.tasks._flush_debounced_messages.apply_async")
    @patch("shops.selectors.get_shop_settings", return_value=None)
    @patch("shops.selectors.get_shop")
    @patch("chat.services.bot_state.bot_state_is_human_active", return_value=False)
    def test_redis_down_processes_immediately(
        self, mock_bot_state, mock_get_shop, mock_get_settings,
        mock_flush_async, mock_is_greeting, mock_run_ai_turn, mock_send_text
    ):
        """When Redis is unavailable, the message should still be processed
        immediately as a single-message turn."""
        mock_get_shop.return_value = _mock_shop(self.shop_id)

        # Make get_redis_connection raise an exception inside _debounce_and_schedule
        with patch("chat.tasks.get_redis_connection", side_effect=ConnectionError("Redis down")):
            process_inbound_message(
                shop_id=self.shop_id,
                page_id="test_page",
                psid="redis_down_user",
                message_text="Help me please",
                mid="mid.1",
                timestamp=1000,
                page_access_token="token",
                channel="FACEBOOK",
            )

        # The flush task should NOT have been scheduled (Redis was down)
        mock_flush_async.assert_not_called()

        # run_ai_turn should have been called directly (fallback path)
        mock_run_ai_turn.assert_called_once()
        _, kwargs = mock_run_ai_turn.call_args
        self.assertEqual(kwargs["inbound_text"], "Help me please")

        # The reply should have been sent
        mock_send_text.assert_called_once()
