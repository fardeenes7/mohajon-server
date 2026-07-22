from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.utils import OperationalError, ProgrammingError

from ai.models import AIModelRegistry, AIModelUsage


@dataclass(frozen=True)
class ResolvedAIModel:
    usage: str
    provider: str
    model_name: str
    input_price_per_1m_tokens: Decimal | None = None
    output_price_per_1m_tokens: Decimal | None = None
    image_price_per_call: Decimal | None = None


_FALLBACK_MODELS: dict[str, ResolvedAIModel] = {
    AIModelUsage.CHAT_COMPLETION: ResolvedAIModel(
        usage=AIModelUsage.CHAT_COMPLETION,
        provider="GOOGLE",
        # gemini-2.5-flash-lite is the cheapest Google text model that is
        # reachable on the Vercel gateway's free tier; the 3.x-flash-lite
        # models are gated behind paid credits. Keep the emergency fallback on
        # a model that actually answers so a DB-down bootstrap still works.
        model_name="google/gemini-2.5-flash-lite",
        input_price_per_1m_tokens=Decimal("0.10"),
        output_price_per_1m_tokens=Decimal("0.40"),
    ),
    AIModelUsage.EMBEDDING: ResolvedAIModel(
        usage=AIModelUsage.EMBEDDING,
        provider="GOOGLE",
        model_name="google/gemini-embedding-2",
        input_price_per_1m_tokens=Decimal("0.20"),
    ),
    AIModelUsage.IMAGE_GENERATION: ResolvedAIModel(
        usage=AIModelUsage.IMAGE_GENERATION,
        provider="OPENAI",
        model_name="openai/gpt-image-1",
    ),
}


def resolve_ai_model(*, usage: str) -> ResolvedAIModel:
    """
    Resolve active model config by usage with DB-first, safe-fallback behavior.

    Query order:
      1) Active default row for usage
      2) Active row by smallest priority
      3) Hardcoded safe fallback
    """
    fallback = _FALLBACK_MODELS[usage]

    try:
        active_qs = AIModelRegistry.objects.filter(
            usage=usage,
            is_active=True,
            deleted_at__isnull=True,
        )

        model_row = (
            active_qs.filter(is_default=True).order_by("priority", "id").first()
            or active_qs.order_by("priority", "id").first()
        )
        if not model_row:
            return fallback

        return ResolvedAIModel(
            usage=model_row.usage,
            provider=model_row.provider,
            model_name=model_row.model_name,
            input_price_per_1m_tokens=model_row.input_price_per_1m_tokens,
            output_price_per_1m_tokens=model_row.output_price_per_1m_tokens,
            image_price_per_call=model_row.image_price_per_call,
        )
    except (ProgrammingError, OperationalError):
        return fallback


def resolve_ai_model_ladder(*, usage: str) -> list[ResolvedAIModel]:
    """
    Resolve the full ordered fallback ladder for a usage.

    The list starts with the active default (if any), followed by the remaining
    active rows in ascending priority. The hardcoded safe fallback is always
    appended last (deduplicated by model_name) so the caller can keep trying
    working models when the gateway rejects the preferred one (e.g. a free-tier
    403/429 on a restricted model).

    Callers should attempt each entry in order and stop at the first success.
    """
    fallback = _FALLBACK_MODELS[usage]

    def _to_resolved(row) -> ResolvedAIModel:
        return ResolvedAIModel(
            usage=row.usage,
            provider=row.provider,
            model_name=row.model_name,
            input_price_per_1m_tokens=row.input_price_per_1m_tokens,
            output_price_per_1m_tokens=row.output_price_per_1m_tokens,
            image_price_per_call=row.image_price_per_call,
        )

    ladder: list[ResolvedAIModel] = []
    seen: set[str] = set()

    try:
        active_qs = AIModelRegistry.objects.filter(
            usage=usage,
            is_active=True,
            deleted_at__isnull=True,
        )
        default_row = active_qs.filter(is_default=True).order_by("priority", "id").first()
        if default_row:
            ladder.append(_to_resolved(default_row))
            seen.add(default_row.model_name)

        for row in active_qs.order_by("priority", "id"):
            if row.model_name in seen:
                continue
            ladder.append(_to_resolved(row))
            seen.add(row.model_name)
    except (ProgrammingError, OperationalError):
        return [fallback]

    if fallback.model_name not in seen:
        ladder.append(fallback)

    return ladder or [fallback]
