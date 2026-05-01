from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.conf import settings
from openai import OpenAI, APIError, APIConnectionError, RateLimitError

from core.models import AIModelUsage, AIModelProvider, AIUsageLog
from core.services.ai_model_registry import resolve_ai_model, ResolvedAIModel
from core.services.ai_credits import calculate_credits, deduct_ai_credits

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

    def __init__(self, shop_id: str, reference_id: str | None = None):
        self.shop_id = shop_id
        self.reference_id = reference_id
        self._client_cache: dict[str, Any] = {}

    def _get_client(self, provider: str) -> Any:
        if provider not in self._client_cache:
            if provider == AIModelProvider.OPENAI:
                self._client_cache[provider] = OpenAI(
                    api_key=settings.OPENAI_API_KEY,
                    base_url=self._OPENAI_BASE_URL,
                )
            else:
                # TODO: Add Anthropic, Stability, etc.
                raise ValueError(f"Unsupported AI provider: {provider}")
        return self._client_cache[provider]

    def call_chat_completion(
        self, 
        messages: list[dict[str, Any]], 
        usage_type: str = AIModelUsage.CHAT_COMPLETION,
        **kwargs
    ) -> str:
        """
        Execute a standard chat completion and deduct credits.
        """
        from core.services.ai_credits import has_sufficient_ai_credits
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
    # Tool-calling interface (used by messenger.services.ai_engine)
    # ------------------------------------------------------------------

    def call_chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
        usage_type: str = AIModelUsage.CHAT_COMPLETION,
        _retry_attempt: int = 0,
    ) -> Any:
        """
        Execute a single OpenAI request with tool schemas and return the raw
        ChatCompletion response object.  The caller is responsible for
        orchestrating the multi-turn tool-call loop.

        Token accumulation and credit deduction happen via the caller using
        log_accumulated_usage() after the loop completes.

        Implements a single automatic retry on transient 5xx / connection
        errors (global_business_rules_and_limits.md §5 retry policy).
        """
        import time

        model_config = resolve_ai_model(usage=usage_type)
        client = self._get_client(model_config.provider)

        try:
            return client.chat.completions.create(
                model=model_config.model_name,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
            )
        except (APIError, APIConnectionError, RateLimitError) as exc:
            if _retry_attempt == 0:
                time.sleep(2)
                return self.call_chat_with_tools(
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    usage_type=usage_type,
                    _retry_attempt=1,
                )
            logger.error("AI Gateway Tool-Call Error (shop=%s): %s", self.shop_id, exc)
            raise

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
        model_config = resolve_ai_model(usage=usage_type)

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
        from core.services.ai_credits import has_sufficient_ai_credits
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
        """
        model_config = resolve_ai_model(usage=AIModelUsage.EMBEDDING)
        client = self._get_client(model_config.provider)

        try:
            response = client.embeddings.create(
                model=model_config.model_name,
                input=text,
                **kwargs
            )
            
            # Handle credits
            usage = response.usage
            if usage:
                # text-embedding-3-small is usually $0.02 / 1M tokens
                rate = model_config.input_price_per_1m_tokens or Decimal("0.02")
                from core.services.ai_credits import calculate_credits
                
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

        except Exception as e:
            logger.error("AI Gateway Embedding Error (shop=%s): %s", self.shop_id, e)
            raise

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
