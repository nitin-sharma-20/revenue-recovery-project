"""
tests/integration/test_llm_provider_openai_live.py

Live-API integration test for the OpenAI provider path.
ALWAYS skipped unless RUN_LIVE_LLM_TESTS=1 is set.
Requires OPENAI_API_KEY in environment.

Phase 3 — implement when LangChainLLMClient is complete.
"""

import pytest


@pytest.mark.live_llm
def test_openai_provider_returns_valid_action():
    """
    Calls the real OpenAI API via LangChainLLMClient and asserts:
      - Returns a valid RecoveryActionEnum value
      - Does not raise LLMProviderExhausted (since OpenAI should succeed)
      - recommendation_source starts with "llm_openai"

    Phase 3 stub.
    """
    pytest.skip("Phase 3 stub — implement after LangChainLLMClient is complete")
