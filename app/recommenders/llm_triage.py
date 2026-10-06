"""
app/recommenders/llm_triage.py — LLM triage for unknown-bucket events (flag_on only).

This module is ONLY invoked when llm_triage_enabled=True and the event's root-cause
bucket is 'unknown'. For all other cases, rule_based.py handles recommendation.

ARCHITECTURE RULES:
  1. This file MUST NOT import from app.policy_engine or app.executor.
  2. It returns (action, reasoning, source) and nothing else.
  3. It raises LLMProviderExhausted when all providers fail — it does NOT catch that
     exception. pipeline.py catches it and converts to escalate_human with
     recommendation_source="llm_fallback_all_failed".
  4. LLM calls execute inside a ThreadPoolExecutor (max 4 workers, 30s timeout).
     This constraint is enforced at the call site in rule_based.py / pipeline.py,
     not inside this module.

LLM client is dependency-injected (LLMClientProtocol). Tests use FakeLLMClient.
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from app.recommenders.protocol import LLMClientProtocol, LLMProviderExhausted

if TYPE_CHECKING:
    from app.models import PaymentEvent

logger = logging.getLogger(__name__)


import time
from pydantic import BaseModel, Field
from typing import Literal

def triage_unknown_event(
    event: "PaymentEvent",
    llm_client: LLMClientProtocol,
    previous_attempts: int,
    hours_since_failure: float,
) -> tuple[str, str, str]:
    """
    Calls the LLM client to triage an unknown-bucket event.

    Returns: (action, reasoning, source_label)
    Raises:  LLMProviderExhausted — caught in pipeline.py, not here.
    """
    action, reasoning, source = llm_client.recommend(
        root_cause_bucket="unknown",
        amount=event.amount,
        error_code=event.failure_reason_code,
        error_description=event.failure_reason_raw,
        previous_attempts=previous_attempts,
        hours_since_failure=hours_since_failure
    )
    return action, reasoning, source


class TriageRecommendation(BaseModel):
    action: Literal["retry_now", "retry_later", "switch_method", "escalate_human", "stop"] = Field(
        description="The recommended recovery action."
    )
    reasoning: str = Field(
        description="The reasoning behind the recommendation."
    )


class LangChainLLMClient:
    """
    Production LLM client implementing LLMClientProtocol.
    Uses LangChain with structured Pydantic output.

    Provider fallback chain: OpenAI -> Groq -> LLMProviderExhausted
    Retry policy per provider: 3 attempts, backoff 10s/30s/60s.
    No bare except: pass anywhere (DESIGN_DECISIONS.md item 10).
    """

    def __init__(
        self,
        openai_api_key: Optional[str] = None,
        groq_api_key: Optional[str] = None,
    ):
        self.openai_api_key = openai_api_key
        self.groq_api_key = groq_api_key

    def _call_provider_with_retry(self, provider_name: str, model_name: str, api_key: str, prompt: str) -> tuple[str, str]:
        """Calls a single provider with exponential backoff (10s, 30s, 60s) for rate limits."""
        import os
        from langchain_core.prompts import PromptTemplate
        
        # We handle imports here so if keys aren't provided we don't crash on module load
        if provider_name == "openai":
            from langchain_openai import ChatOpenAI
            from openai import RateLimitError
            llm = ChatOpenAI(model=model_name, api_key=api_key, temperature=0)
            rate_limit_exc = RateLimitError
        elif provider_name == "groq":
            from langchain_groq import ChatGroq
            from groq import RateLimitError
            llm = ChatGroq(model=model_name, api_key=api_key, temperature=0)
            rate_limit_exc = RateLimitError
        else:
            raise ValueError(f"Unknown provider: {provider_name}")

        structured_llm = llm.with_structured_output(TriageRecommendation)
        
        delays = [10, 30, 60]
        attempts = len(delays)
        
        last_err = None
        for attempt in range(attempts):
            try:
                response = structured_llm.invoke(prompt)
                return response.action, response.reasoning
            except rate_limit_exc as e:
                last_err = e
                delay = delays[attempt]
                logger.warning(f"RateLimitError on {provider_name} attempt {attempt + 1}: {repr(e)}. Sleeping {delay}s...")
                time.sleep(delay)
            except Exception as e:
                # Other exceptions log and fall through
                logger.warning(f"{provider_name} failed with non-rate-limit error: {repr(e)}")
                raise e

        logger.error(f"{provider_name}_exhausted attempts={attempts} final_err={repr(last_err)}")
        raise last_err

    def recommend(
        self,
        root_cause_bucket: str,
        amount: float,
        error_code: Optional[str],
        error_description: Optional[str],
        previous_attempts: int,
        hours_since_failure: float,
    ) -> tuple[str, str, str]:
        
        prompt = f"""
You are a recovery policy decision engine. Analyze this failed payment:
Bucket: {root_cause_bucket}
Amount: INR {amount}
Error Code: {error_code}
Error Message: {error_description}
Previous Attempts: {previous_attempts}
Hours Since Failure: {hours_since_failure}

You must recommend exactly one action from the following list:
- retry_now
- retry_later
- switch_method
- escalate_human
- stop

Provide the action and brief reasoning.
"""
        
        last_error = None
        
        # 1. Try OpenAI
        if self.openai_api_key:
            try:
                action, reasoning = self._call_provider_with_retry(
                    provider_name="openai",
                    model_name="gpt-4o-mini",
                    api_key=self.openai_api_key,
                    prompt=prompt
                )
                return action, reasoning, "llm_openai"
            except Exception as e:
                logger.warning(f"openai_failed err={repr(e)}")
                last_error = e
        
        # 2. Fallback to Groq
        if self.groq_api_key:
            try:
                action, reasoning = self._call_provider_with_retry(
                    provider_name="groq",
                    model_name="qwen/qwen3.8-27b",
                    api_key=self.groq_api_key,
                    prompt=prompt
                )
                return action, reasoning, "llm_groq"
            except Exception as e:
                logger.warning(f"groq_failed err={repr(e)}")
                last_error = e

        # 3. All providers exhausted
        logger.error("All LLM providers exhausted.")
        raise LLMProviderExhausted("All configured LLM providers failed.") from last_error
