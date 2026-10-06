"""
app/recommenders/protocol.py — Shared types and protocol for recommender callables.

All recommenders (baseline_a, rule_based, llm_triage) implement these interfaces.
The LLMClientProtocol is the dependency-injection boundary for LLM calls —
FakeLLMClient vs LangChainLLMClient, both satisfy it.

ARCHITECTURE RULE: This file may import from app.policy_engine ONLY to reference
RecoveryActionEnum for type safety. It may NEVER call PolicyEngine.evaluate().
"""

from __future__ import annotations

import logging
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# ── LLM Client Protocol (dependency injection boundary) ───────────────────────

@runtime_checkable
class LLMClientProtocol(Protocol):
    """
    Protocol that both LangChainLLMClient and FakeLLMClient must satisfy.
    evaluate_and_execute() accepts any object implementing this protocol.

    Tests inject FakeLLMClient by default — no API calls on a normal pytest run.
    Live-API tests in tests/integration/ are gated behind RUN_LIVE_LLM_TESTS=1.
    """

    def recommend(
        self,
        root_cause_bucket: str,
        amount: float,
        error_code: Optional[str],
        error_description: Optional[str],
        previous_attempts: int,
        hours_since_failure: float,
    ) -> tuple[str, str, str]:
        """
        Returns: (action: str, reasoning: str, source: str)
        action must be one of the RecoveryActionEnum values.
        Raises LLMProviderExhausted if all configured providers fail.
        """
        ...


class LLMProviderExhausted(Exception):
    """
    Raised when every configured LLM provider has failed for a given event.

    ARCHITECTURE RULE (DESIGN_DECISIONS.md item 10):
      This exception is caught in pipeline.py — NOT in the recommender.
      The pipeline converts it to recommended_action="escalate_human" with
      recommendation_source="llm_fallback_all_failed" and proceeds through
      the normal Policy Engine path. It is NEVER silently swallowed.
    """


# ── Fake client for tests ─────────────────────────────────────────────────────

class FakeLLMClient:
    """
    Deterministic test double for LLMClientProtocol.
    Never makes a network call.
    Configure per-test via constructor args or pytest fixtures.

    Usage in tests:
        fake = FakeLLMClient(default_action="retry_later")
        result = evaluate_and_execute(event, ..., llm_client=fake)

    To simulate LLMProviderExhausted in tests:
        fake = FakeLLMClient(raise_exhausted=True)
    """

    def __init__(
        self,
        default_action: str = "escalate_human",
        default_reasoning: str = "FakeLLMClient deterministic response",
        default_source: str = "llm_fake",
        raise_exhausted: bool = False,
    ):
        self.default_action = default_action
        self.default_reasoning = default_reasoning
        self.default_source = default_source
        self.raise_exhausted = raise_exhausted
        self.call_count = 0

    def recommend(
        self,
        root_cause_bucket: str,
        amount: float,
        error_code: Optional[str],
        error_description: Optional[str],
        previous_attempts: int,
        hours_since_failure: float,
    ) -> tuple[str, str, str]:
        self.call_count += 1
        if self.raise_exhausted:
            raise LLMProviderExhausted(
                "FakeLLMClient configured to simulate all-providers-exhausted."
            )
        return self.default_action, self.default_reasoning, self.default_source
