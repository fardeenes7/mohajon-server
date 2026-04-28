from django.test import SimpleTestCase
from unittest.mock import MagicMock, patch

from mohajon.celery import app as celery_app


class CeleryAiIsolationConfigTests(SimpleTestCase):
	def test_ai_queues_are_declared(self) -> None:
		queue_names = {queue.name for queue in celery_app.conf.task_queues}

		self.assertIn("ai_rag", queue_names)
		self.assertIn("ai_copy", queue_names)
		self.assertIn("ai_image", queue_names)
		self.assertIn("messenger", queue_names)

	def test_ai_routes_are_registered(self) -> None:
		task_routes = celery_app.conf.task_routes

		self.assertEqual(task_routes["messenger.tasks.embed_faq_entry"]["queue"], "ai_rag")
		self.assertEqual(task_routes["core.tasks.generate_product_copy"]["queue"], "ai_copy")
		self.assertEqual(task_routes["core.tasks.generate_ad_copy"]["queue"], "ai_copy")
		self.assertEqual(task_routes["core.tasks.generate_ad_image"]["queue"], "ai_image")

	def test_baseline_ai_tasks_have_expected_queue_metadata(self) -> None:
		from core.tasks import generate_ad_copy, generate_ad_image, generate_product_copy

		self.assertEqual(generate_product_copy.queue, "ai_copy")
		self.assertEqual(generate_ad_copy.queue, "ai_copy")
		self.assertEqual(generate_ad_image.queue, "ai_image")


class AIModelRegistryResolverTests(SimpleTestCase):
	@patch("core.services.ai_model_registry.AIModelRegistry.objects")
	def test_resolve_ai_model_returns_fallback_when_no_active_row(self, objects_mock) -> None:
		from core.models import AIModelUsage
		from core.services.ai_model_registry import resolve_ai_model

		active_qs = MagicMock()
		default_qs = MagicMock()

		objects_mock.filter.return_value = active_qs
		active_qs.filter.return_value = default_qs
		default_qs.order_by.return_value = default_qs
		default_qs.first.return_value = None
		active_qs.order_by.return_value = active_qs
		active_qs.first.return_value = None

		resolved = resolve_ai_model(usage=AIModelUsage.CHAT_COMPLETION)

		self.assertEqual(resolved.model_name, "gpt-4o-mini")
		self.assertEqual(resolved.provider, "OPENAI")

	@patch("core.services.ai_model_registry.AIModelRegistry.objects")
	def test_resolve_ai_model_prefers_active_default(self, objects_mock) -> None:
		from decimal import Decimal

		from core.models import AIModelUsage
		from core.services.ai_model_registry import resolve_ai_model

		active_qs = MagicMock()
		default_qs = MagicMock()
		row = MagicMock()
		row.usage = AIModelUsage.CHAT_COMPLETION
		row.provider = "OPENAI"
		row.model_name = "gpt-4o"
		row.input_price_per_1m_tokens = Decimal("0.0025")
		row.output_price_per_1m_tokens = Decimal("0.01")
		row.image_price_per_call = None

		objects_mock.filter.return_value = active_qs
		active_qs.filter.return_value = default_qs
		default_qs.order_by.return_value = default_qs
		default_qs.first.return_value = row

		resolved = resolve_ai_model(usage=AIModelUsage.CHAT_COMPLETION)

		self.assertEqual(resolved.model_name, "gpt-4o")
		self.assertEqual(resolved.input_price_per_1m_tokens, Decimal("0.0025"))

	@patch("core.services.ai_model_registry.AIModelRegistry.objects")
	def test_resolve_ai_model_handles_db_bootstrap_errors(self, objects_mock) -> None:
		from django.db import ProgrammingError

		from core.models import AIModelUsage
		from core.services.ai_model_registry import resolve_ai_model

		objects_mock.filter.side_effect = ProgrammingError("relation does not exist")

		resolved = resolve_ai_model(usage=AIModelUsage.EMBEDDING)

		self.assertEqual(resolved.model_name, "text-embedding-3-small")
		self.assertEqual(resolved.provider, "OPENAI")
