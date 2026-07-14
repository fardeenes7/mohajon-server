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
        provider="OPENAI",
        model_name="gpt-4o-mini",
        input_price_per_1m_tokens=Decimal("0.00015"),
        output_price_per_1m_tokens=Decimal("0.0006"),
    ),
    AIModelUsage.EMBEDDING: ResolvedAIModel(
        usage=AIModelUsage.EMBEDDING,
        provider="OPENAI",
        model_name="text-embedding-3-small",
    ),
    AIModelUsage.IMAGE_GENERATION: ResolvedAIModel(
        usage=AIModelUsage.IMAGE_GENERATION,
        provider="OPENAI",
        model_name="gpt-image-1",
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
