import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import PaymentEvent, Decision, ActionTaken
from app.recommenders.rule_based import make_ablation_recommender

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

def test_recommender_cross_condition_run_isolation(db_session):
    """
    Verifies that a recommender only counts prior attempts for its own condition and run_id.
    """
    now = datetime.now(timezone.utc)
    
    test_event = PaymentEvent(
        razorpay_payment_id="pay_iso_001",
        amount=100.0,
        currency="INR",
        created_at=now - timedelta(days=1)
    )
    db_session.add(test_event)
    db_session.commit()
    
    # Add a prior attempt for flag_off in run-001
    dec_old = Decision(
        event_id=test_event.id,
        condition="flag_off",
        run_id="run-001",
        recommended_action="retry_now",
        reasoning="first attempt"
    )
    db_session.add(dec_old)
    db_session.commit()
    
    act_old = ActionTaken(
        decision_id=dec_old.id,
        action_type="retry_now",
        idempotency_key="key_1",
        executed_at=now - timedelta(hours=5),
        razorpay_response="{}"
    )
    db_session.add(act_old)
    db_session.commit()

    # Now create a recommender for flag_on in run-002
    # It should NOT see the flag_off attempt.
    # We use a soft_decline so we can test its behavior based on previous attempts.
    # If previous_attempts == 0, soft_decline returns retry_now.
    # If previous_attempts > 0, it returns retry_later.
    
    from app.root_cause import RootCauseClassification
    rc = RootCauseClassification(
        event_id=test_event.id,
        bucket="soft_decline",
        classified_by="rule"
    )
    db_session.add(rc)
    db_session.commit()
    
    rec_fn = make_ablation_recommender(
        llm_triage_enabled=True,
        condition_label="flag_on",
        run_id="run-002",
        llm_client=None
    )
    
    action, reasoning, source = rec_fn(test_event, db_session)
    
    # Since it should see 0 attempts, it should return retry_now, not retry_later
    assert action == "retry_now"
