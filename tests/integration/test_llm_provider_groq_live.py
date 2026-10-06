"""
tests/integration/test_llm_provider_groq_live.py

Live-API integration test for the Groq provider path.
ALWAYS skipped unless RUN_LIVE_LLM_TESTS=1 is set.
Requires GROQ_API_KEY in environment.

Phase 3 — implement when LangChainLLMClient is complete.
"""

import pytest


@pytest.mark.live_llm
def test_groq_provider_returns_valid_action():
    """
    Calls the real Groq API via LangChainLLMClient and asserts:
      - Returns a valid RecoveryActionEnum value
      - Does not raise LLMProviderExhausted (since Groq should succeed)
      - recommendation_source starts with "llm_groq"

    Phase 3 stub.
    """
    pytest.skip("Phase 3 stub — implement after LangChainLLMClient is complete")


@pytest.mark.live_llm
def test_groq_provider_exhausted_raises_named_exception():
    """
    With an invalid API key, asserts that LLMProviderExhausted is raised
    (not a bare exception or silent fallback).

    Phase 3 stub.
    """
    pytest.skip("Phase 3 stub — implement after LangChainLLMClient is complete")
