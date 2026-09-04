"""
Acceptance Test for Phase 2:
Runs Strategy A (Naive Baseline) and Strategy B (Rule-Only Policy) on the 'dev' split.
Asserts that both strategies produce traceable outcome rows with zero errors.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import PaymentEvent, Decision, PolicyVerdict, ActionTaken, Outcome, RootCauseClassification
from app.strategies.strategy_a_baseline import run_strategy_a
from app.strategies.strategy_b_rules_only import run_strategy_b
from app.strategies.strategy_c_llm_policy import run_strategy_c


@pytest.fixture
def test_db_session():
    """Provides a fresh isolated in-memory SQLite database session."""
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


def load_dev_events_from_dataset(session) -> list[PaymentEvent]:
    """Loads only the 'dev' split records from data/dataset.json into the DB session."""
    dataset_path = Path("data/dataset.json")
    splits_path = Path("data/splits.json")

    assert dataset_path.exists(), "dataset.json must exist"
    assert splits_path.exists(), "splits.json must exist"

    with open(dataset_path, "r", encoding="utf-8") as f:
        all_records = json.load(f)

    with open(splits_path, "r", encoding="utf-8") as f:
        splits = json.load(f)

    dev_payment_ids = set(splits["dev"])
    dev_events = []

    for r in all_records:
        if r["razorpay_payment_id"] in dev_payment_ids:
            event = PaymentEvent(
                razorpay_payment_id=r["razorpay_payment_id"],
                amount=r["amount"],
                currency=r["currency"],
                failure_reason_raw=r["failure_reason_raw"],
                failure_reason_code=r["failure_reason_code"],
                customer_id=r["customer_id"],
                order_id=r["order_id"],
                split_bucket="dev",
                created_at=datetime.fromisoformat(r["created_at"])
            )
            session.add(event)
            dev_events.append(event)

    session.commit()
    return dev_events


def test_strategy_a_dev_split_acceptance(test_db_session):
    """
    Acceptance test for Strategy A on dev split:
    Produces outcome rows for all dev records without error.
    """
    dev_events = load_dev_events_from_dataset(test_db_session)
    assert len(dev_events) == 120, "Expected 120 dev records (80% of 150)"

    results = run_strategy_a(dev_events, test_db_session)

    assert results["total_events"] == 120
    assert results["total_attempts"] == 120
    assert results["recovered_count"] > 0
    assert results["total_amount_recovered"] > 0.0

    # Verify database persistence and traceability
    outcomes = test_db_session.query(Outcome).filter_by(strategy="A").all()
    assert len(outcomes) == 120
    decisions = test_db_session.query(Decision).filter_by(strategy="A").all()
    assert len(decisions) == 120


def test_strategy_b_dev_split_acceptance(test_db_session):
    """
    Acceptance test for Strategy B on dev split:
    Produces classification, decision, verdict, action, and outcome rows without error.
    """
    dev_events = load_dev_events_from_dataset(test_db_session)
    assert len(dev_events) == 120

    results = run_strategy_b(dev_events, test_db_session)

    assert results["total_events"] == 120
    assert results["recovered_count"] > 0
    assert results["total_amount_recovered"] > 0.0

    # Verify database persistence across all tables
    outcomes = test_db_session.query(Outcome).filter_by(strategy="B").all()
    assert len(outcomes) == 120
    decisions = test_db_session.query(Decision).filter_by(strategy="B").all()
    assert len(decisions) == 120
    verdicts = test_db_session.query(PolicyVerdict).all()
    assert len(verdicts) == 120
    classifications = test_db_session.query(RootCauseClassification).all()
    assert len(classifications) == 120

    # Verify that hard_decline, risky, and unknown had 0 retry attempts
    blocked_event_ids = [
        c.event_id for c in classifications if c.bucket in ["hard_decline", "risky", "unknown"]
    ]
    for eid in blocked_event_ids:
        outcome = test_db_session.query(Outcome).filter_by(event_id=eid, strategy="B").first()
        assert outcome.attempts_used == 0
        assert outcome.recovered is False


def test_strategy_c_dev_split_acceptance(test_db_session):
    """
    Acceptance test for Strategy C on dev split:
    Produces decisions, verdicts, actions, and outcomes for the full dev set with no unhandled exceptions.
    """
    dev_events = load_dev_events_from_dataset(test_db_session)
    assert len(dev_events) == 120

    results = run_strategy_c(dev_events, test_db_session)

    assert results["total_events"] == 120
    
    # Verify database persistence across all tables
    outcomes = test_db_session.query(Outcome).filter_by(strategy="C").all()
    assert len(outcomes) == 120
    decisions = test_db_session.query(Decision).filter_by(strategy="C").all()
    assert len(decisions) == 120
    
    # Verdicts and actions should exist for C
    verdicts = test_db_session.query(PolicyVerdict).join(Decision).filter(Decision.strategy == "C").all()
    assert len(verdicts) == 120


def test_strategy_comparison_on_dev(test_db_session):
    """
    Compares Strategy A, B, and C on dev split.
    Strategy B should have a higher recovery efficiency than A.
    Strategy C is evaluated against B.
    """
    dev_events = load_dev_events_from_dataset(test_db_session)
    
    res_a = run_strategy_a(dev_events, test_db_session)
    res_b = run_strategy_b(dev_events, test_db_session)
    res_c = run_strategy_c(dev_events, test_db_session)

    print(f"\n[DEV SPLIT] Strategy A: INR {res_a['total_amount_recovered']:.2f} recovered across {res_a['total_attempts']} attempts (INR {res_a['recovery_per_intervention']}/intervention)")
    print(f"[DEV SPLIT] Strategy B: INR {res_b['total_amount_recovered']:.2f} recovered across {res_b['total_attempts']} attempts (INR {res_b['recovery_per_intervention']}/intervention)")
    print(f"[DEV SPLIT] Strategy C: INR {res_c['total_amount_recovered']:.2f} recovered across {res_c['total_attempts']} attempts (INR {res_c['recovery_per_intervention']}/intervention)")
    print(f"            - Strategy C Source Breakdown: {res_c['llm_decisions']} LLM | {res_c['fallback_decisions']} Fallback")

    assert res_b["recovery_per_intervention"] >= res_a["recovery_per_intervention"]
