"""
LLM Recommendation Layer for Reclaim.
Uses LangChain with constrained structured Pydantic output.
Recommends recovery actions strictly within the fixed action enum.
DOES NOT execute payments or make policy decisions.
"""

from datetime import datetime, timezone
import json
import os
from typing import Optional
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser

from app.policy_engine import RecoveryActionEnum
from app.config import settings


class RecoveryRecommendation(BaseModel):
    """Structured recovery recommendation output schema."""
    action: RecoveryActionEnum = Field(
        description="The recommended recovery action. Must be one of: retry_now, retry_later, switch_method, escalate_human, stop."
    )
    reasoning: str = Field(
        description="Clear, concise rationale explaining why this action was recommended for the failure."
    )


SYSTEM_PROMPT = """You are Reclaim's expert AI payment recovery advisor.
Your role is to analyze a failed payment event and recommend the most effective recovery intervention.

You MUST choose your action ONLY from the following allowed set:
1. `retry_now` - Safe to retry immediately (e.g. transient network or gateway timeouts).
2. `retry_later` - Retry with a delayed backoff window (e.g. temporary issuer declines, insufficient account balance allowing replenishment).
3. `switch_method` - Prompt customer to switch to another payment instrument (e.g. invalid card, expired card, blocked debit).
4. `escalate_human` - Flag for manual review / customer support (e.g. potential fraud, high-value anomalies).
5. `stop` - Cease all retry activity (e.g. unrecoverable decline, customer cancelled).

CRITICAL INSTRUCTIONS:
- You must return a valid JSON object matching the requested schema.
- Select exactly ONE action from the allowed list.
- Provide a clear, actionable reasoning explaining your choice.
"""

HUMAN_PROMPT = """Failed Payment Details:
- Root Cause Classification: {root_cause_bucket}
- Payment Amount: INR {amount:.2f}
- Error Code: {error_code}
- Error Description: {error_description}
- Number of Previous Attempts: {previous_attempts}
- Failure Age: {hours_since_failure:.1f} hours

Recommend the best recovery action and explain your reasoning."""


def build_recommendation_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", HUMAN_PROMPT)
    ])


def recommend_action_heuristics(
    root_cause_bucket: str,
    amount: float,
    error_code: Optional[str] = None,
    error_description: Optional[str] = None,
    previous_attempts: int = 0,
    hours_since_failure: float = 0.0
) -> RecoveryRecommendation:
    """
    Deterministic rule-based fallback / mock recommender used when LLM API keys
    are not configured, ensuring 100% testability and offline reproducibility.
    """
    bucket = (root_cause_bucket or "").lower()
    
    if bucket == "hard_decline":
        # Expired / invalid card is best recovered by prompting customer to switch payment method
        if previous_attempts == 0:
            return RecoveryRecommendation(
                action=RecoveryActionEnum.SWITCH_METHOD,
                reasoning="Permanent card invalidity detected. Recommend prompting customer to switch payment method to UPI or alternative card."
            )
        return RecoveryRecommendation(
            action=RecoveryActionEnum.STOP,
            reasoning="Hard decline persistent across attempts. Stopping recovery to eliminate merchant fees."
        )

    elif bucket == "risky":
        return RecoveryRecommendation(
            action=RecoveryActionEnum.ESCALATE_HUMAN,
            reasoning="High-risk fraud score flagged by gateway risk engine. Immediate human review required."
        )

    elif bucket == "network_error":
        if previous_attempts == 0 and hours_since_failure < 1.0:
            return RecoveryRecommendation(
                action=RecoveryActionEnum.RETRY_NOW,
                reasoning="Transient network connection timeout. Immediate retry has high likelihood of success."
            )
        return RecoveryRecommendation(
            action=RecoveryActionEnum.RETRY_LATER,
            reasoning="Subsequent network retry scheduled with cooldown."
        )

    elif bucket == "insufficient_funds":
        if amount > 10000.0 and previous_attempts >= 1:
            return RecoveryRecommendation(
                action=RecoveryActionEnum.SWITCH_METHOD,
                reasoning="High-ticket transaction with insufficient funds. Recommend switching payment method to credit line or EMI."
            )
        return RecoveryRecommendation(
            action=RecoveryActionEnum.RETRY_LATER,
            reasoning="Account balance insufficient. Delayed backoff allows customer time to replenish funds."
        )

    elif bucket == "soft_decline":
        if previous_attempts >= 2:
            return RecoveryRecommendation(
                action=RecoveryActionEnum.SWITCH_METHOD,
                reasoning="Repeated issuer decline. Recommend switching to alternate payment instrument."
            )
        return RecoveryRecommendation(
            action=RecoveryActionEnum.RETRY_LATER,
            reasoning="Temporary issuer decline. Schedule delayed retry allowing issuer cooldown."
        )

    return RecoveryRecommendation(
        action=RecoveryActionEnum.ESCALATE_HUMAN,
        reasoning="Uncertain failure pattern. Escalating to human review."
    )


def get_llm_recommendation(
    root_cause_bucket: str,
    amount: float,
    error_code: Optional[str] = None,
    error_description: Optional[str] = None,
    previous_attempts: int = 0,
    created_at: Optional[datetime] = None,
    current_time: Optional[datetime] = None
) -> tuple[RecoveryRecommendation, str]:
    """
    Calls LangChain LLM with structured output schema to get an action recommendation.
    Falls back gracefully to intelligent deterministic recommender if API keys are missing.
    """
    now = current_time or datetime.now(timezone.utc)
    hours_since_failure = 0.0
    if created_at:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        hours_since_failure = max(0.0, (now - created_at).total_seconds() / 3600.0)

    # Check for API keys
    api_key_openai = os.environ.get("OPENAI_API_KEY") or settings.OPENAI_API_KEY
    api_key_google = os.environ.get("GOOGLE_API_KEY") or settings.GOOGLE_API_KEY
    api_key_groq = os.environ.get("GROQ_API_KEY") or settings.GROQ_API_KEY

    # If LangChain model can be instantiated:
    if api_key_openai:
        try:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.0, api_key=api_key_openai)
            structured_llm = llm.with_structured_output(RecoveryRecommendation)
            prompt = build_recommendation_prompt()
            chain = prompt | structured_llm
            result = chain.invoke({
                "root_cause_bucket": root_cause_bucket,
                "amount": amount,
                "error_code": error_code or "UNKNOWN",
                "error_description": error_description or "None",
                "previous_attempts": previous_attempts,
                "hours_since_failure": hours_since_failure
            })
            print("Served by OpenAI")
            return result, "llm (openai)"
        except Exception as e:
            pass

    if api_key_google:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model="gemini-pro", temperature=0.0, google_api_key=api_key_google.strip())
            structured_llm = llm.with_structured_output(RecoveryRecommendation)
            prompt = build_recommendation_prompt()
            chain = prompt | structured_llm
            result = chain.invoke({
                "root_cause_bucket": root_cause_bucket,
                "amount": amount,
                "error_code": error_code or "UNKNOWN",
                "error_description": error_description or "None",
                "previous_attempts": previous_attempts,
                "hours_since_failure": hours_since_failure
            })
            print("Served by Google GenAI")
            return result, "llm (google)"
        except Exception as e:
            print(f"Google GenAI error: {e}")

    if api_key_groq:
        import time
        max_retries = 3
        for attempt in range(max_retries):
            try:
                from langchain_groq import ChatGroq
                llm = ChatGroq(model="openai/gpt-oss-120b", temperature=0.0, groq_api_key=api_key_groq, request_timeout=30)
                structured_llm = llm.with_structured_output(RecoveryRecommendation)
                prompt = build_recommendation_prompt()
                chain = prompt | structured_llm
                result = chain.invoke({
                    "root_cause_bucket": root_cause_bucket,
                    "amount": amount,
                    "error_code": error_code or "UNKNOWN",
                    "error_description": error_description or "None",
                    "previous_attempts": previous_attempts,
                    "hours_since_failure": hours_since_failure
                })
                print("Served by Groq")
                return result, "llm (groq)"
            except Exception as e:
                if "429" in str(e) or "rate_limit" in str(e).lower():
                    print(f"Groq Rate limit hit, sleeping for 45 seconds (Attempt {attempt+1}/{max_retries})")
                    time.sleep(45)
                else:
                    print(f"Groq error: {e}")
                    break

    # Use deterministic recommendation engine when running offline/in test harness
    return recommend_action_heuristics(
        root_cause_bucket=root_cause_bucket,
        amount=amount,
        error_code=error_code,
        error_description=error_description,
        previous_attempts=previous_attempts,
        hours_since_failure=hours_since_failure
    ), "fallback_heuristic"
