from django.test import SimpleTestCase, TestCase
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

		self.assertEqual(task_routes["chat.tasks.embed_faq_entry"]["queue"], "ai_rag")
		self.assertEqual(task_routes["ai.tasks.generate_product_copy"]["queue"], "ai_copy")
		self.assertEqual(task_routes["ai.tasks.generate_ad_copy"]["queue"], "ai_copy")
		self.assertEqual(task_routes["ai.tasks.generate_ad_image"]["queue"], "ai_image")

	def test_baseline_ai_tasks_have_expected_queue_metadata(self) -> None:
		from ai.tasks import generate_ad_copy, generate_ad_image, generate_product_copy

		self.assertEqual(generate_product_copy.queue, "ai_copy")
		self.assertEqual(generate_ad_copy.queue, "ai_copy")
		self.assertEqual(generate_ad_image.queue, "ai_image")


class AIModelRegistryResolverTests(SimpleTestCase):
	@patch("ai.services.ai_model_registry.AIModelRegistry.objects")
	def test_resolve_ai_model_returns_fallback_when_no_active_row(self, objects_mock) -> None:
		from ai.models import AIModelUsage
		from ai.services.ai_model_registry import resolve_ai_model

		active_qs = MagicMock()
		default_qs = MagicMock()

		objects_mock.filter.return_value = active_qs
		active_qs.filter.return_value = default_qs
		default_qs.order_by.return_value = default_qs
		default_qs.first.return_value = None
		active_qs.order_by.return_value = active_qs
		active_qs.first.return_value = None

		resolved = resolve_ai_model(usage=AIModelUsage.CHAT_COMPLETION)

		self.assertEqual(resolved.model_name, "google/gemini-2.5-flash-lite")
		self.assertEqual(resolved.provider, "GOOGLE")

	@patch("ai.services.ai_model_registry.AIModelRegistry.objects")
	def test_resolve_ai_model_prefers_active_default(self, objects_mock) -> None:
		from decimal import Decimal

		from ai.models import AIModelUsage
		from ai.services.ai_model_registry import resolve_ai_model

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

	@patch("ai.services.ai_model_registry.AIModelRegistry.objects")
	def test_resolve_ai_model_handles_db_bootstrap_errors(self, objects_mock) -> None:
		from django.db import ProgrammingError

		from ai.models import AIModelUsage
		from ai.services.ai_model_registry import resolve_ai_model

		objects_mock.filter.side_effect = ProgrammingError("relation does not exist")

		resolved = resolve_ai_model(usage=AIModelUsage.EMBEDDING)

		self.assertEqual(resolved.model_name, "google/gemini-embedding-2")
		self.assertEqual(resolved.provider, "GOOGLE")


def _gateway_fixture():
    """A trimmed, realistic slice of the Vercel AI gateway /models response."""
    return [
        # google text: the handcoded default (2.5-flash-lite, free-tier reachable)
        # plus the paid-tier lites that sit below it in the ladder.
        {
            "id": "google/gemini-2.5-flash-lite",
            "type": "language",
            "name": "Gemini 2.5 Flash Lite",
            "modalities": {"input": ["text", "image", "pdf"], "output": ["text"]},
            "context_window": 1000000,
            "pricing": {"input": "0.0000001", "output": "0.0000004"},
        },
        {
            "id": "google/gemini-3.5-flash-lite",
            "type": "language",
            "name": "Gemini 3.5 Flash Lite",
            "modalities": {"input": ["text", "image", "pdf"], "output": ["text"]},
            "context_window": 1000000,
            "pricing": {"input": "0.0000003", "output": "0.0000025"},
        },
        {
            "id": "google/gemini-3.1-flash-lite",
            "type": "language",
            "name": "Gemini 3.1 Flash Lite",
            "modalities": {"input": ["text"], "output": ["text"]},
            "pricing": {"input": "0.00000025", "output": "0.0000015"},
        },
        # openai text fallbacks
        {
            "id": "openai/gpt-5-mini",
            "type": "language",
            "name": "GPT-5 mini",
            "modalities": {"input": ["text"], "output": ["text"]},
            "pricing": {"input": "0.00000025", "output": "0.000002"},
        },
        {
            "id": "openai/gpt-5.4-nano",
            "type": "language",
            "name": "GPT-5.4 nano",
            "modalities": {"input": ["text"], "output": ["text"]},
            "pricing": {"input": "0.0000002", "output": "0.00000125"},
        },
        # google embedding (the handcoded embedding default)
        {
            "id": "google/gemini-embedding-2",
            "type": "embedding",
            "name": "Gemini Embedding 2",
            "modalities": {"input": ["text"], "output": ["text"]},
            "pricing": {"input": "0.0000002"},
        },
        # openai embedding (synced, but not a default)
        {
            "id": "openai/text-embedding-3-small",
            "type": "embedding",
            "name": "text-embedding-3-small",
            "modalities": {"input": ["text"], "output": ["text"]},
            "pricing": {"input": "0.00000002"},
        },
        # must be SKIPPED: multimodal image output typed as "language"
        {
            "id": "google/gemini-3.5-flash-image",
            "type": "language",
            "name": "Gemini 3.5 Flash Image",
            "modalities": {"input": ["text"], "output": ["text", "image"]},
            "pricing": {"input": "0.0000003", "output": "0.0000025"},
        },
        # must be SKIPPED: image type
        {
            "id": "openai/gpt-image-1",
            "type": "image",
            "name": "GPT Image 1",
            "modalities": {"input": ["text"], "output": ["image"]},
            "pricing": {"input": "0.000005", "output": "0.00004"},
        },
        # must be SKIPPED: provider not google/openai
        {
            "id": "alibaba/qwen-3-14b",
            "type": "language",
            "name": "Qwen3-14B",
            "modalities": {"input": ["text"], "output": ["text"]},
            "pricing": {"input": "0.00000012", "output": "0.00000024"},
        },
    ]


class SyncAIModelsTests(TestCase):
    def _sync(self, **kwargs):
        from ai.services.model_sync import sync_ai_models

        return sync_ai_models(models=_gateway_fixture(), **kwargs)

    def test_only_google_openai_text_and_embedding_are_synced(self):
        from ai.models import AIModelRegistry, AIModelProvider, AIModelUsage

        stats = self._sync()

        rows = AIModelRegistry.objects.all()
        names = set(rows.values_list("model_name", flat=True))
        # 5 text + 2 embedding = 7 kept; image/other-provider skipped.
        self.assertEqual(rows.count(), 7)
        self.assertNotIn("google/gemini-3.5-flash-image", names)
        self.assertNotIn("openai/gpt-image-1", names)
        self.assertNotIn("alibaba/qwen-3-14b", names)
        self.assertEqual(stats.created, 7)
        self.assertEqual(stats.skipped, 3)

        providers = set(rows.values_list("provider", flat=True))
        self.assertTrue(providers <= {AIModelProvider.GOOGLE, AIModelProvider.OPENAI})

    def test_capabilities_recorded_in_metadata(self):
        from ai.models import AIModelRegistry, AIModelUsage

        self._sync()

        text_row = AIModelRegistry.objects.get(model_name="google/gemini-3.5-flash-lite")
        self.assertEqual(text_row.usage, AIModelUsage.CHAT_COMPLETION)
        self.assertEqual(text_row.metadata["capability"], "text")

        emb_row = AIModelRegistry.objects.get(model_name="google/gemini-embedding-2")
        self.assertEqual(emb_row.usage, AIModelUsage.EMBEDDING)
        self.assertEqual(emb_row.metadata["capability"], "embedding")

    def test_pricing_converted_to_per_million_tokens(self):
        from decimal import Decimal
        from ai.models import AIModelRegistry

        self._sync()

        row = AIModelRegistry.objects.get(model_name="google/gemini-3.5-flash-lite")
        # 0.0000003 USD/token -> 0.30 USD / 1M tokens
        self.assertEqual(row.input_price_per_1m_tokens, Decimal("0.300000"))
        self.assertEqual(row.output_price_per_1m_tokens, Decimal("2.500000"))

    def test_handcoded_defaults_and_fallback_priority(self):
        from ai.models import AIModelRegistry, AIModelUsage

        self._sync()

        text_default = AIModelRegistry.objects.get(
            usage=AIModelUsage.CHAT_COMPLETION, is_default=True
        )
        self.assertEqual(text_default.model_name, "google/gemini-2.5-flash-lite")

        emb_default = AIModelRegistry.objects.get(
            usage=AIModelUsage.EMBEDDING, is_default=True
        )
        self.assertEqual(emb_default.model_name, "google/gemini-embedding-2")

        # Fallbacks ranked in declared order (lower priority = preferred).
        p = {
            r.model_name: r.priority
            for r in AIModelRegistry.objects.filter(usage=AIModelUsage.CHAT_COMPLETION)
        }
        self.assertLess(p["google/gemini-2.5-flash-lite"], p["google/gemini-3.5-flash-lite"])
        self.assertLess(p["google/gemini-3.5-flash-lite"], p["google/gemini-3.1-flash-lite"])
        self.assertLess(p["google/gemini-3.1-flash-lite"], p["openai/gpt-5-mini"])
        self.assertLess(p["openai/gpt-5-mini"], p["openai/gpt-5.4-nano"])

    def test_resolver_picks_handcoded_text_default(self):
        from ai.models import AIModelUsage
        from ai.services.ai_model_registry import resolve_ai_model

        self._sync()

        resolved = resolve_ai_model(usage=AIModelUsage.CHAT_COMPLETION)
        self.assertEqual(resolved.model_name, "google/gemini-2.5-flash-lite")
        self.assertEqual(resolved.provider, "GOOGLE")

    def test_resync_preserves_admin_routing_but_refreshes_price(self):
        from decimal import Decimal
        from ai.models import AIModelRegistry, AIModelUsage

        self._sync()

        # Admin overrides the default and deactivates a model.
        admin_choice = AIModelRegistry.objects.get(model_name="openai/gpt-5-mini")
        AIModelRegistry.objects.filter(
            usage=AIModelUsage.CHAT_COMPLETION, is_default=True
        ).update(is_default=False)
        admin_choice.is_default = True
        admin_choice.priority = 1
        admin_choice.save()

        # Gateway changes a price on re-sync.
        models = _gateway_fixture()
        for m in models:
            if m["id"] == "openai/gpt-5-mini":
                m["pricing"]["input"] = "0.0000005"

        from ai.services.model_sync import sync_ai_models

        stats = sync_ai_models(models=models)
        self.assertEqual(stats.created, 0)
        self.assertEqual(stats.updated, 7)

        admin_choice.refresh_from_db()
        # Admin's routing choices survive the re-sync...
        self.assertTrue(admin_choice.is_default)
        self.assertEqual(admin_choice.priority, 1)
        # ...but pricing is refreshed. 0.0000005/tok -> 0.50/1M
        self.assertEqual(admin_choice.input_price_per_1m_tokens, Decimal("0.500000"))
        # Handcoded default is NOT re-applied because a default already exists.
        self.assertFalse(
            AIModelRegistry.objects.get(
                model_name="google/gemini-2.5-flash-lite"
            ).is_default
        )

    def test_no_defaults_flag_skips_default_seeding(self):
        from ai.models import AIModelRegistry, AIModelUsage

        self._sync(apply_defaults=False)

        self.assertFalse(
            AIModelRegistry.objects.filter(is_default=True).exists()
        )


class ResolveAIModelLadderTests(TestCase):
    def _sync(self, **kwargs):
        from ai.services.model_sync import sync_ai_models

        return sync_ai_models(models=_gateway_fixture(), **kwargs)

    def test_ladder_is_priority_ordered_with_default_first(self):
        from ai.models import AIModelUsage
        from ai.services.ai_model_registry import resolve_ai_model_ladder

        self._sync()

        ladder = resolve_ai_model_ladder(usage=AIModelUsage.CHAT_COMPLETION)
        names = [m.model_name for m in ladder]

        # Default leads; declared fallbacks follow in order.
        self.assertEqual(names[0], "google/gemini-2.5-flash-lite")
        self.assertEqual(
            names[:5],
            [
                "google/gemini-2.5-flash-lite",
                "google/gemini-3.5-flash-lite",
                "google/gemini-3.1-flash-lite",
                "openai/gpt-5-mini",
                "openai/gpt-5.4-nano",
            ],
        )
        # No duplicates in the ladder.
        self.assertEqual(len(names), len(set(names)))

    def test_ladder_falls_back_to_hardcoded_when_registry_empty(self):
        from ai.models import AIModelUsage
        from ai.services.ai_model_registry import resolve_ai_model_ladder

        # No sync — registry has no rows for this usage.
        ladder = resolve_ai_model_ladder(usage=AIModelUsage.CHAT_COMPLETION)

        self.assertEqual(len(ladder), 1)
        self.assertEqual(ladder[0].model_name, "google/gemini-2.5-flash-lite")


class AIGatewayLadderWalkTests(TestCase):
    """The gateway must skip a restricted/rate-limited model and try the next."""

    def _sync(self):
        from ai.services.model_sync import sync_ai_models

        return sync_ai_models(models=_gateway_fixture())

    def test_403_on_default_walks_to_next_working_model(self):
        from unittest.mock import MagicMock, patch
        from openai import PermissionDeniedError

        from ai.services.ai_gateway import AIGateway

        self._sync()

        # First model 403s (free-tier restricted); second one answers.
        good_response = MagicMock()
        good_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)

        denied = PermissionDeniedError(
            message="restricted", response=MagicMock(status_code=403), body=None
        )

        create_mock = MagicMock(side_effect=[denied, good_response])
        fake_client = MagicMock()
        fake_client.chat.completions.create = create_mock

        gw = AIGateway(shop_id="shop-1", reference_id="psid-1")
        with patch.object(gw, "_get_client", return_value=fake_client):
            resp = gw.call_chat_with_tools(messages=[], tools=[])

        self.assertIs(resp, good_response)
        # Recorded the model that actually answered (the 2nd rung), not the 1st.
        self.assertEqual(create_mock.call_count, 2)
        self.assertEqual(
            gw._last_used_model.model_name, "google/gemini-3.5-flash-lite"
        )
