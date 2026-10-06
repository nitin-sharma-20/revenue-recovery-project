"""
tests/conftest.py — Global pytest fixtures and markers.

Key responsibilities:
  1. Register @pytest.mark.live_llm marker and enforce skip unless RUN_LIVE_LLM_TESTS=1.
     A normal pytest run will NEVER make an API call (DESIGN_DECISIONS.md item 5).
  2. Export FakeLLMClient fixture for use across all test files.
  3. Provide in-memory SQLite session fixture for unit tests.
"""

from __future__ import annotations

import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# ── Live LLM marker enforcement ───────────────────────────────────────────────

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live_llm: mark test as requiring a live LLM API (skipped by default; "
        "set RUN_LIVE_LLM_TESTS=1 to run)."
    )


@pytest.fixture(autouse=True)
def skip_live_llm_unless_enabled(request):
    """
    Autouse fixture: skips any test marked @pytest.mark.live_llm unless the
    environment variable RUN_LIVE_LLM_TESTS=1 is set.

    This is the enforcement gate from DESIGN_DECISIONS.md item 5.
    """
    if request.node.get_closest_marker("live_llm"):
        if not os.environ.get("RUN_LIVE_LLM_TESTS"):
            pytest.skip("Live LLM test skipped (set RUN_LIVE_LLM_TESTS=1 to run)")


# ── FakeLLMClient fixture ─────────────────────────────────────────────────────

@pytest.fixture
def fake_llm_client():
    """
    Default deterministic fake LLM client.
    Returns escalate_human for all inputs. No network calls.
    """
    from app.recommenders.protocol import FakeLLMClient
    return FakeLLMClient(default_action="escalate_human")


@pytest.fixture
def fake_llm_client_exhausted():
    """
    Fake LLM client configured to raise LLMProviderExhausted.
    Used to test the all-providers-failed -> escalate_human path
    (DESIGN_DECISIONS.md item 10, Phase 3 gate).
    """
    from app.recommenders.protocol import FakeLLMClient
    return FakeLLMClient(raise_exhausted=True)


# ── In-memory database session fixture ───────────────────────────────────────

@pytest.fixture
def db_session():
    """
    Fresh in-memory SQLite session for unit tests.
    Each test gets a clean database — no cross-test state.
    """
    from app.db import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
