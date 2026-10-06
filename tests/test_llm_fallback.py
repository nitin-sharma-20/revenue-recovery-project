import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import PaymentEvent, Decision, Outcome, RootCauseClassification
from app.pipeline import evaluate_and_execute
from app.recommenders.rule_based import make_ablation_recommender
from app.recommenders.protocol import FakeLLMClient

@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)

def test_pipeline_catches_llm_provider_exhausted(db_session):
    """
    Proves that when the LLM client exhausts all providers and raises LLMProviderExhausted,
    the pipeline.py orchestrator catches it, converts to escalate_human, and records
    'llm_fallback_all_failed' as the recommendation_source.
    """
    now = datetime.now(timezone.utc)
    
    test_event = PaymentEvent(
        razorpay_payment_id="pay_exhaustion_test",
        amount=500.0,
        currency="INR",
        created_at=now - timedelta(days=2),
        instrument_type="mandate"
    )
    db_session.add(test_event)
    db_session.commit()
    
    # We must prepopulate root cause since pipeline doesn't have an error code that forces 'unknown' easily,
    # or we can just pass an empty error code which falls back to unknown.
    test_event.failure_reason_code = "UNKNOWN_ERROR_CODE_XYZ"
    test_event.failure_reason_raw = "weird error"
    
    # Configure FakeLLMClient to raise LLMProviderExhausted
    failing_client = FakeLLMClient(raise_exhausted=True)
    
    recommender = make_ablation_recommender(
        llm_triage_enabled=True,
        condition_label="flag_on",
        run_id="test_run_exhausted",
        llm_client=failing_client
    )
    
    # Execute
    result = evaluate_and_execute(
        event=test_event,
        recommender=recommender,
        condition_label="flag_on",
        run_id="test_run_exhausted",
        db=db_session
    )
    
    # Assert pipeline caught it and set the fallback correctly
    assert result.recommended_action == "escalate_human"
    assert result.recommendation_source == "llm_fallback_all_failed"
    assert "Fallback: All configured LLM providers failed" in result.reasoning
    
    # Verify DB recorded it
    decision = db_session.query(Decision).filter_by(event_id=test_event.id, run_id="test_run_exhausted").first()
    assert decision.recommended_action == "escalate_human"
    assert decision.recommendation_source == "llm_fallback_all_failed"
