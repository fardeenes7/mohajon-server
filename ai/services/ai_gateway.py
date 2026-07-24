from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.conf import settings
from openai import (
    OpenAI,
    APIError,
    APIConnectionError,
    RateLimitError,
    PermissionDeniedError,
)

from ai.models import AIModelUsage, AIModelProvider, AIUsageLog
from ai.services.ai_model_registry import (
    resolve_ai_model,
    resolve_ai_model_ladder,
    ResolvedAIModel,
)
from ai.services.ai_credits import calculate_credits, deduct_ai_credits

logger = logging.getLogger(__name__)


class AIGateway:
    """
    Unified entry point for all AI model invocations (EPIC A-02).

    Handles:
      1. Model resolution via registry
      2. Client initialization (single shared client per instance)
      3. Credit check, deduction, and AIUsageLog auditing (post-call)

    For multi-turn tool-calling flows (e.g. Messenger AI engine), callers
    should use call_chat_with_tools() in a loop and then call
    log_accumulated_usage() once after the loop to write a single audit
    record for the entire turn.
    """

    _OPENAI_BASE_URL = "https://ai-gateway.vercel.sh/v1"
    # External embedding provider (OpenAI-compatible endpoint) used as the primary
    # embedding route.  Configured via EMBEDDING_PROVIDER_URL + EMBEDDING_PROVIDER_API_KEY.
    # Falls back to the Vercel AI gateway when not configured or on failure.

    def __init__(self, shop_id: str, reference_id: str | None = None):
        self.shop_id = shop_id
        self.reference_id = reference_id
        self._client_cache: dict[str, Any] = {}
        # The model config that actually produced the last successful response.
        # The ladder walk may fall past a restricted/rate-limited model, so this
        # records what really ran for accurate credit accounting + audit logging.
        self._last_used_model: ResolvedAIModel | None = None

    def _get_client(self, provider: str) -> Any:
        if provider not in self._client_cache:
            if provider in (AIModelProvider.OPENAI, AIModelProvider.GOOGLE):
                # Both providers are served through the Vercel AI gateway with
                # namespaced model ids ("openai/…", "google/…"), so the client
                # is the same OpenAI-compatible client regardless of provider.
                self._client_cache[provider] = OpenAI(
                    api_key=settings.OPENAI_API_KEY,
                    base_url=self._OPENAI_BASE_URL,
                )
            else:
                # TODO: Add Anthropic, Stability, etc.
                raise ValueError(f"Unsupported AI provider: {provider}")
        return self._client_cache[provider]

    def _get_embedding_provider_client(self) -> Any | None:
        """
        Lazy-initialize the external embedding provider client.
        Returns None when EMBEDDING_PROVIDER_URL is not configured, which
        gracefully disables the provider so dev/test environments work without
        extra credentials.
        """
        _KEY = "__embedding_provider"
        if _KEY not in self._client_cache:
            url = getattr(settings, "EMBEDDING_PROVIDER_URL", "")
            key = getattr(settings, "EMBEDDING_PROVIDER_API_KEY", "")
            if url and key:
                self._client_cache[_KEY] = OpenAI(
                    api_key=key,
                    base_url=url,
                )
            else:
                self._client_cache[_KEY] = None
        return self._client_cache["__embedding_provider"]

    def call_chat_completion(
        self, 
        messages: list[dict[str, Any]], 
        usage_type: str = AIModelUsage.CHAT_COMPLETION,
        **kwargs
    ) -> str:
        """
        Execute a standard chat completion and deduct credits.
        """
        from ai.services.ai_credits import has_sufficient_ai_credits
        if not has_sufficient_ai_credits(shop_id=self.shop_id):
            raise ValueError("Insufficient AI credits to perform this request.")

        model_config = resolve_ai_model(usage=usage_type)
        client = self._get_client(model_config.provider)

        try:
            response = client.chat.completions.create(
                model=model_config.model_name,
                messages=messages,
                **kwargs
            )
            
            # Handle credits
            usage = response.usage
            if usage:
                input_rate = model_config.input_price_per_1m_tokens or Decimal("0.15") # fallback to mini
                output_rate = model_config.output_price_per_1m_tokens or Decimal("0.60")
                
                credits_to_deduct, usd_cost = calculate_credits(
                    model_input_rate=input_rate,
                    model_output_rate=output_rate,
                    input_tokens=usage.prompt_tokens,
                    output_tokens=usage.completion_tokens
                )
                deduct_ai_credits(shop_id=self.shop_id, credits=credits_to_deduct)
                
                self._log_usage(
                    usage_type=usage_type,
                    model_config=model_config,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    usd_cost=usd_cost,
                    credits_deducted=credits_to_deduct
                )

            return response.choices[0].message.content or ""

        except Exception as e:
            logger.error("AI Gateway Chat Error (shop=%s): %s", self.shop_id, e)
            raise

    # ------------------------------------------------------------------
    # Tool-calling interface (used by chat.services.ai_engine)
    # ------------------------------------------------------------------

    def call_chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
        usage_type: str = AIModelUsage.CHAT_COMPLETION,
    ) -> Any:
        """
        Execute a single OpenAI request with tool schemas and return the raw
        ChatCompletion response object.  The caller is responsible for
        orchestrating the multi-turn tool-call loop.

        Token accumulation and credit deduction happen via the caller using
        log_accumulated_usage() after the loop completes.

        Resilience (global_business_rules_and_limits.md §5 retry policy):
          - Walks the registry fallback ladder (default → priority-ordered
            fallbacks → hardcoded safe model). A restricted/unavailable model
            (403) or a rate-limited one (429) is skipped so the turn keeps
            trying working models instead of dying on the preferred one.
          - Transient 5xx / connection errors get one in-place retry per model
            before moving to the next rung.
          - The model that actually answered is recorded on
            ``self._last_used_model`` so credit accounting reflects reality.
        """
        import time

        ladder = resolve_ai_model_ladder(usage=usage_type)
        last_exc: Exception | None = None

        for index, model_config in enumerate(ladder):
            client = self._get_client(model_config.provider)
            for attempt in range(2):  # one in-place retry for transient errors
                try:
                    response = client.chat.completions.create(
                        model=model_config.model_name,
                        messages=messages,
                        tools=tools,
                        tool_choice=tool_choice,
                    )
                    self._last_used_model = model_config
                    if index > 0:
                        logger.info(
                            "AI Gateway (shop=%s) used fallback model %s (rung %d/%d)",
                            self.shop_id, model_config.model_name, index + 1, len(ladder),
                        )
                    return response
                except (PermissionDeniedError, RateLimitError) as exc:
                    # 403 (model not accessible) / 429 (rate-limited): retrying
                    # the same model won't help — move to the next rung.
                    last_exc = exc
                    logger.warning(
                        "AI Gateway (shop=%s) model %s unavailable (%s); trying next fallback.",
                        self.shop_id, model_config.model_name,
                        type(exc).__name__,
                    )
                    break
                except (APIError, APIConnectionError) as exc:
                    # Transient server/connection error: retry the same model
                    # once, then fall through to the next rung.
                    last_exc = exc
                    if attempt == 0:
                        time.sleep(2)
                        continue
                    logger.warning(
                        "AI Gateway (shop=%s) model %s errored (%s); trying next fallback.",
                        self.shop_id, model_config.model_name, exc,
                    )
                    break

        logger.error(
            "AI Gateway Tool-Call Error (shop=%s): all %d fallback model(s) failed; last error: %s",
            self.shop_id, len(ladder), last_exc,
        )
        raise last_exc if last_exc else RuntimeError("No AI models available")

    def resolve_chat_model(self, usage_type: str = AIModelUsage.CHAT_COMPLETION) -> ResolvedAIModel:
        """
        Expose the resolved model config so callers can access pricing rates
        for credit accumulation without duplicating registry logic.
        """
        return resolve_ai_model(usage=usage_type)

    def log_accumulated_usage(
        self,
        *,
        usage_type: str,
        total_input_tokens: int,
        total_output_tokens: int,
    ) -> None:
        """
        Write a single AIUsageLog entry for a completed multi-turn session
        and deduct credits atomically.  Call this once after a tool-call
        loop has finished rather than logging per-turn.
        """
        # Prefer the model that actually answered — the ladder walk may have
        # fallen past a restricted/rate-limited model to a cheaper/pricier one,
        # so re-resolving the default here would bill the wrong rate.
        model_config = self._last_used_model or resolve_ai_model(usage=usage_type)

        input_rate = model_config.input_price_per_1m_tokens or Decimal("0.15")
        output_rate = model_config.output_price_per_1m_tokens or Decimal("0.60")

        credits_to_deduct, usd_cost = calculate_credits(
            model_input_rate=input_rate,
            model_output_rate=output_rate,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
        )

        if credits_to_deduct > 0:
            deduct_ai_credits(shop_id=self.shop_id, credits=credits_to_deduct)

        self._log_usage(
            usage_type=usage_type,
            model_config=model_config,
            prompt_tokens=total_input_tokens,
            completion_tokens=total_output_tokens,
            usd_cost=usd_cost,
            credits_deducted=credits_to_deduct,
        )

    def call_image_generation(self, prompt: str, **kwargs) -> str:
        """
        Execute image generation and deduct credits.
        Returns the image URL.
        """
        from ai.services.ai_credits import has_sufficient_ai_credits
        # Images cost significantly more (e.g. 4 credits), but we check for at least 1 for consistency
        if not has_sufficient_ai_credits(shop_id=self.shop_id):
            raise ValueError("Insufficient AI credits to perform this request.")

        model_config = resolve_ai_model(usage=AIModelUsage.IMAGE_GENERATION)
        client = self._get_client(model_config.provider)

        try:
            response = client.images.generate(
                model=model_config.model_name,
                prompt=prompt,
                **kwargs
            )
            
            # Handle credits (per call for images)
            price_per_call = model_config.image_price_per_call or Decimal("4.00") # $0.04 -> 4 credits
            usd_cost = price_per_call * Decimal("0.01")
            deduct_ai_credits(shop_id=self.shop_id, credits=price_per_call)
            
            self._log_usage(
                usage_type=AIModelUsage.IMAGE_GENERATION,
                model_config=model_config,
                prompt_tokens=0,
                completion_tokens=0,
                usd_cost=usd_cost,
                credits_deducted=price_per_call,
                metadata={"prompt": prompt}
            )

            return response.data[0].url or ""

        except Exception as e:
            logger.error("AI Gateway Image Error (shop=%s): %s", self.shop_id, e)
            raise

    def call_embedding(self, text: str, **kwargs) -> list[float]:
        """
        Generate embeddings for a piece of text.
        Vector creation is FREE and does NOT deduct AI credits.

        Resolution order:
          1. Embedding provider (EMBEDDING_PROVIDER_URL / EMBEDDING_PROVIDER_API_KEY).
             The registry model name (e.g. "google/gemini-embedding-2") is translated to
             the provider's slug convention: "google/" → "google-ai-studio/".
          2. Vercel AI gateway — used as the fallback when the provider is not
             configured or the call fails. Uses the namespaced model id as-is.
        """
        model_config = resolve_ai_model(usage=AIModelUsage.EMBEDDING)

        # Resolve the model name for the embedding provider.
        # EMBEDDING_PROVIDER_MODEL takes priority — set it explicitly when the
        # provider's naming convention differs from the registry (e.g. a different
        # provider slug prefix).  When blank, we derive it automatically from the
        # registry model name by swapping the Vercel-style "google/" prefix for
        # the Cloudflare/google-ai-studio slug prefix "google-ai-studio/".
        _provider_model_name = getattr(settings, "EMBEDDING_PROVIDER_MODEL", model_config.model_name)

        def _do_embed(client: Any, model_name: str) -> list[float]:
            kwargs.setdefault("encoding_format", "float")
            response = client.embeddings.create(
                model=model_name,
                input=text,
                **kwargs
            )

            # Audit log (fire-and-forget, no credit deduction for embeddings)
            usage = response.usage
            if usage:
                rate = model_config.input_price_per_1m_tokens or Decimal("0.02")
                from ai.services.ai_credits import calculate_credits
                _, usd_cost = calculate_credits(
                    model_input_rate=rate,
                    model_output_rate=Decimal("0"),
                    input_tokens=usage.prompt_tokens,
                    output_tokens=0
                )
                self._log_usage(
                    usage_type=AIModelUsage.EMBEDDING,
                    model_config=model_config,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=0,
                    usd_cost=usd_cost,
                    credits_deducted=Decimal("0")
                )

            return response.data[0].embedding

        # ── 1. Embedding provider (primary) ───────────────────────────────────
        provider_client = self._get_embedding_provider_client()
        _provider_exc: Exception | None = None  # survives the except block (Python 3 deletes `as` vars on exit)
        if provider_client is not None:
            try:
                return _do_embed(provider_client, _provider_model_name)
            except Exception as exc:
                _provider_exc = exc  # capture before `exc` is deleted
                logger.warning(
                    "Embedding provider failed (shop=%s, model=%s): %s — falling back to AI gateway.",
                    self.shop_id, _provider_model_name, _provider_exc,
                )

        # ── 2. Vercel AI gateway (fallback) ───────────────────────────────────
        gateway_client = self._get_client(model_config.provider)
        try:
            result = _do_embed(gateway_client, model_config.model_name)
            if provider_client is not None:
                # Only log as fallback when provider was attempted first
                logger.info(
                    "Embedding (shop=%s): AI gateway fallback succeeded (model=%s).",
                    self.shop_id, model_config.model_name,
                )
            return result
        except Exception as gateway_exc:
            if provider_client is not None:
                logger.error(
                    "Embedding Error (shop=%s): both embedding provider and AI gateway failed. "
                    "Provider: %s | Gateway: %s",
                    self.shop_id, _provider_exc, gateway_exc,
                )
            else:
                logger.error(
                    "Embedding Error (shop=%s): AI gateway failed (no provider configured): %s",
                    self.shop_id, gateway_exc,
                )
            raise gateway_exc

    def _log_usage(
        self,
        *,
        usage_type: str,
        model_config: ResolvedAIModel,
        prompt_tokens: int,
        completion_tokens: int,
        usd_cost: Decimal,
        credits_deducted: Decimal,
        metadata: dict | None = None
    ) -> None:
        """Create a background log entry for usage auditing."""
        try:
            # We use .create() directly; for ultra-high scale this could be moved to a task
            AIUsageLog.objects.create(
                tenant_id=self.shop_id,
                shop_id=self.shop_id,
                usage_type=usage_type,
                provider=model_config.provider,
                model_name=model_config.model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                usd_cost=usd_cost,
                credits_deducted=credits_deducted,
                reference_id=self.reference_id,
                metadata=metadata or {}
            )
        except Exception as exc:
            logger.error("AI Gateway Logging Error (shop=%s): %s", self.shop_id, exc)
