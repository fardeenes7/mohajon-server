from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from django.conf import settings
from django.db import transaction

from ai.models import AIModelProvider, AIModelRegistry, AIModelUsage

logger = logging.getLogger(__name__)

# ── Gateway wiring ────────────────────────────────────────────────────────────
# Models are served through the Vercel AI Gateway (same base URL the runtime
# AIGateway talks to), so ``model_name`` stores the fully-namespaced id
# (e.g. "google/gemini-3.5-flash-lite") that gets passed straight to the client.
_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"

# We only sync text + embedding models from these two providers, per product
# requirements. The gateway id is namespaced "<provider>/<model>", and the
# provider prefix maps to our AIModelProvider choices.
_PROVIDER_PREFIXES: dict[str, str] = {
    "google": AIModelProvider.GOOGLE,
    "openai": AIModelProvider.OPENAI,
}

# Gateway "type" -> our usage + capability tag. Anything not in this map
# (image, video, audio, transcription, realtime, speech) is skipped.
_TYPE_TO_USAGE: dict[str, str] = {
    "language": AIModelUsage.CHAT_COMPLETION,
    "embedding": AIModelUsage.EMBEDDING,
}
_USAGE_CAPABILITY: dict[str, str] = {
    AIModelUsage.CHAT_COMPLETION: "text",
    AIModelUsage.EMBEDDING: "embedding",
}

# ── Handcoded first-time defaults & fallbacks ────────────────────────────────
# These seed the registry on the very first sync. They are applied ONLY when a
# usage has no existing default, so platform admins can override the routing
# from the Django admin afterward and re-syncs won't stomp their choices.
# Kept here (not in the DB) intentionally: the synchronizer is the source of
# the *initial* preference ordering; the admin panel owns it thereafter.
#
# NOTE: gemini-2.5-flash-lite leads the ladder because it is the cheapest Google
# text model reachable on the Vercel gateway's free tier — the 3.x-flash-lite
# models require paid credits (403 on free tier). Once the gateway account has
# paid credits, an admin can promote 3.5-flash-lite from the Django admin; the
# runtime gateway also walks this ladder automatically on a 403/429.
DEFAULT_TEXT_MODEL = "google/gemini-2.5-flash-lite"
DEFAULT_TEXT_FALLBACKS = [
    "google/gemini-3.5-flash-lite",
    "google/gemini-3.1-flash-lite",
    "openai/gpt-5-mini",
    "openai/gpt-5.4-nano",
]
DEFAULT_EMBEDDING_MODEL = "google/gemini-embedding-2"

# priority ladder: default first, then fallbacks in declared order. Lower =
# preferred (matches resolve_ai_model ordering by ascending priority).
_DEFAULT_PRIORITY = 10
_FALLBACK_PRIORITY_START = 20
_FALLBACK_PRIORITY_STEP = 10
# Anything the gateway returns that isn't in our handcoded ladder is synced but
# parked at a low preference so it never silently becomes a fallback.
_UNRANKED_PRIORITY = 1000

# 1M-token scaling for the registry's per-1M price columns (gateway reports
# per-single-token USD prices as strings).
_PER_MILLION = Decimal("1000000")


@dataclass
class SyncStats:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    defaults_set: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "defaults_set": self.defaults_set,
            "errors": self.errors,
        }


def _build_client():
    """Instantiate the OpenAI-compatible client pointed at the Vercel gateway."""
    from openai import OpenAI

    api_key = settings.OPENAI_API_KEY
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured; cannot reach the AI gateway to sync models."
        )
    return OpenAI(api_key=api_key, base_url=_GATEWAY_BASE_URL)


def _fetch_gateway_models(client=None) -> list[dict[str, Any]]:
    """Return the raw gateway model dicts (id, type, modalities, pricing, ...)."""
    client = client or _build_client()
    page = client.models.list()
    out: list[dict[str, Any]] = []
    for model in page.data:
        # SDK objects expose model_dump(); fall back to dict() for plain dicts.
        if hasattr(model, "model_dump"):
            out.append(model.model_dump())
        elif isinstance(model, dict):
            out.append(model)
    return out


def _price_per_1m(pricing: dict[str, Any] | None, key: str) -> Decimal | None:
    """Convert a per-token USD price string into a per-1M-token Decimal."""
    if not pricing:
        return None
    raw = pricing.get(key)
    if raw in (None, ""):
        return None
    try:
        return (Decimal(str(raw)) * _PER_MILLION).quantize(Decimal("0.000001"))
    except (ArithmeticError, ValueError):
        return None


def _classify(model: dict[str, Any]) -> tuple[str, str, str] | None:
    """
    Map a gateway model to (provider, usage, model_name) if it's a google/openai
    text or embedding model; otherwise return None (caller counts it skipped).

    A "text" model is a language model that outputs text only — the gateway
    types multimodal image generators (e.g. gemini-*-flash-image) as
    ``language`` too, but they emit images, so we exclude anything whose output
    modality is not purely text.
    """
    model_id = model.get("id") or ""
    prefix, _, _ = model_id.partition("/")
    provider = _PROVIDER_PREFIXES.get(prefix)
    if provider is None:
        return None
    usage = _TYPE_TO_USAGE.get(model.get("type") or "")
    if usage is None:
        return None
    if usage == AIModelUsage.CHAT_COMPLETION:
        output_modalities = ((model.get("modalities") or {}).get("output")) or ["text"]
        if any(m != "text" for m in output_modalities):
            return None
    return provider, usage, model_id


def _upsert_model(model: dict[str, Any], stats: SyncStats) -> None:
    classified = _classify(model)
    if classified is None:
        stats.skipped += 1
        return

    provider, usage, model_name = classified
    pricing = model.get("pricing") or {}
    capability = _USAGE_CAPABILITY[usage]

    metadata = {
        "capability": capability,
        "gateway_type": model.get("type"),
        "modalities": model.get("modalities"),
        "context_window": model.get("context_window"),
        "max_tokens": model.get("max_tokens"),
        "synced_from": "vercel-ai-gateway",
    }

    defaults = {
        "provider": provider,
        "display_name": model.get("name") or model_name,
        "input_price_per_1m_tokens": _price_per_1m(pricing, "input"),
        "output_price_per_1m_tokens": _price_per_1m(pricing, "output"),
        "metadata": metadata,
    }

    existing = AIModelRegistry.all_objects.filter(
        usage=usage, provider=provider, model_name=model_name
    ).first()

    if existing is None:
        AIModelRegistry.objects.create(
            usage=usage,
            model_name=model_name,
            is_active=True,
            is_default=False,
            priority=_UNRANKED_PRIORITY,
            **defaults,
        )
        stats.created += 1
        return

    # Update pricing/metadata/display only. Preserve admin-owned routing state
    # (is_active, is_default, priority) and un-delete a re-appearing model.
    existing.provider = provider
    existing.display_name = defaults["display_name"]
    existing.input_price_per_1m_tokens = defaults["input_price_per_1m_tokens"]
    existing.output_price_per_1m_tokens = defaults["output_price_per_1m_tokens"]
    existing.metadata = metadata
    if existing.deleted_at is not None:
        existing.deleted_at = None
    existing.save(
        update_fields=[
            "provider",
            "display_name",
            "input_price_per_1m_tokens",
            "output_price_per_1m_tokens",
            "metadata",
            "deleted_at",
            "updated_at",
        ]
    )
    stats.updated += 1


def _apply_default_ladder(usage: str, ladder: list[str], stats: SyncStats) -> None:
    """
    Seed the handcoded preference ordering for a usage, but only if that usage
    has no default yet (first-time bootstrap). ``ladder[0]`` becomes the default;
    the rest become priority-ordered fallbacks. Missing models are recorded as
    errors (e.g. the gateway renamed one) rather than silently ignored.
    """
    already_default = AIModelRegistry.objects.filter(
        usage=usage, is_default=True, deleted_at__isnull=True
    ).exists()
    if already_default:
        return

    for index, model_name in enumerate(ladder):
        row = AIModelRegistry.objects.filter(
            usage=usage, model_name=model_name, deleted_at__isnull=True
        ).first()
        if row is None:
            stats.errors.append(
                f"default ladder model not found for {usage}: {model_name}"
            )
            continue

        row.is_active = True
        if index == 0:
            row.is_default = True
            row.priority = _DEFAULT_PRIORITY
            stats.defaults_set.append(f"{usage} default -> {model_name}")
        else:
            row.is_default = False
            row.priority = _FALLBACK_PRIORITY_START + (index - 1) * _FALLBACK_PRIORITY_STEP
        row.save(update_fields=["is_active", "is_default", "priority", "updated_at"])


@transaction.atomic
def sync_ai_models(
    *,
    apply_defaults: bool = True,
    client=None,
    models: Iterable[dict[str, Any]] | None = None,
) -> SyncStats:
    """
    Sync Google + OpenAI text/embedding models from the Vercel AI gateway into
    ``AIModelRegistry``.

    - Upserts every google/openai model of type language (capability "text") or
      embedding (capability "embedding"). Other modalities are skipped.
    - Price columns are converted from the gateway's per-token USD to per-1M.
    - Existing rows only get pricing/metadata/display refreshed; admin-owned
      routing flags (is_active, is_default, priority) are preserved.
    - On first run (``apply_defaults``) seeds the handcoded default/fallback
      ladder, but never overrides a usage that already has a default.

    Args:
        apply_defaults: seed handcoded defaults/fallbacks when a usage has none.
        client: optional pre-built OpenAI client (tests inject a fake).
        models: optional pre-fetched gateway model dicts (bypasses the network).

    Returns:
        SyncStats with created/updated/skipped counts and defaults applied.
    """
    stats = SyncStats()

    gateway_models = list(models) if models is not None else _fetch_gateway_models(client)

    for model in gateway_models:
        try:
            _upsert_model(model, stats)
        except Exception as exc:  # noqa: BLE001 - never abort the whole sync on one row
            model_id = model.get("id", "<unknown>") if isinstance(model, dict) else "<unknown>"
            logger.exception("Failed to sync AI model %s", model_id)
            stats.errors.append(f"{model_id}: {exc}")

    if apply_defaults:
        _apply_default_ladder(
            AIModelUsage.CHAT_COMPLETION,
            [DEFAULT_TEXT_MODEL, *DEFAULT_TEXT_FALLBACKS],
            stats,
        )
        _apply_default_ladder(
            AIModelUsage.EMBEDDING,
            [DEFAULT_EMBEDDING_MODEL],
            stats,
        )

    return stats
