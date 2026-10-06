import pytest
import json
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import PaymentEvent, Decision, Outcome, ActionTaken, PolicyVerdict
from app.pipeline import evaluate_and_execute
from app.recommenders.rule_based import make_ablation_recommender
from data.ground_truth import clear_cache

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

def test_evaluate_and_execute_end_to_end(db_session):
    clear_cache()
    
    dataset_path = Path("data/dataset.json")
    if not dataset_path.exists():
        pytest.skip("Dataset not generated")
        
    with open(dataset_path, "r", encoding="utf-8") as f:
        records = json.load(f)
        
    # Find a highly recoverable record (network_error with mandate)
    record = next(r for r in records if r["bucket"] == "network_error" and r["instrument_type"] == "mandate")
    
    event = PaymentEvent(
        id=record["id"],  # Crucial for get_ground_truth_outcome lookup!
        razorpay_payment_id=record["razorpay_payment_id"],
        amount=record["amount"],
        currency=record["currency"],
        failure_reason_raw=record["failure_reason_raw"],
        failure_reason_code=record["failure_reason_code"],
        customer_id=record["customer_id"],
        order_id=record["order_id"],
        created_at=datetime.fromisoformat(record["created_at"]),
        instrument_type=record["instrument_type"]
    )
    db_session.add(event)
    db_session.commit()
    
    recommender = make_ablation_recommender(
        llm_triage_enabled=False,
        condition_label="flag_off",
        run_id="test_run_1"
    )
    
    result = evaluate_and_execute(
        event=event,
        recommender=recommender,
        condition_label="flag_off",
        run_id="test_run_1",
        db=db_session
    )
    
    assert result.event_id == event.id
    assert result.condition == "flag_off"
    assert result.run_id == "test_run_1"
    
    # Network error should be recommended 'retry_now' and approved
    assert result.recommended_action == "retry_now"
    assert result.verdict_allowed is True
    
    # Verify DB state
    outcome = db_session.query(Outcome).filter_by(event_id=event.id, run_id="test_run_1").first()
    assert outcome is not None
    assert outcome.recovered == result.recovered
    
    action = db_session.query(ActionTaken).first()
    assert action is not None
    assert action.action_type == "retry_now"
    
    decision = db_session.query(Decision).filter_by(event_id=event.id, run_id="test_run_1").first()
    assert decision is not None
    assert decision.recommended_action == "retry_now"
