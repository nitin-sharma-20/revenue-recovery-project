"""
Tests for Executor Idempotency (GEMINI.md Section 9 & 11).
Verifies that calling execute twice with the same (event_id, attempt_number) key
does not produce two actions — the second call is silently deduplicated.
"""

from datetime import datetime, timezone
import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import PaymentEvent, Decision, ActionTaken
from app.executor import (
    generate_idempotency_key,
    execute_retry,
    execute_switch_method,
    execute_escalate_human,
    execute_action,
    count_prior_retry_attempts,
)


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


@pytest.fixture
def sample_event(db_session):
    event = PaymentEvent(
        razorpay_payment_id="pay_test_idem_001",
        amount=1500.0,
        currency="INR",
        failure_reason_raw="Connection to bank gateway timed out",
        failure_reason_code="GATEWAY_ERROR_PAYMENT_TIMED_OUT",
        customer_id="cust_test_001",
        order_id="order_test_001",
        created_at=datetime.now(timezone.utc)
    )
    db_session.add(event)
    db_session.commit()
    return event


@pytest.fixture
def sample_decision(db_session, sample_event):
    decision = Decision(
        event_id=sample_event.id,
        strategy="C",
        recommended_action="retry_now",
        reasoning="Test: LLM recommended immediate retry on network timeout"
    )
    db_session.add(decision)
    db_session.commit()
    return decision


# --- 1. Idempotency Key Generation Tests ---

def test_idempotency_key_deterministic():
    """Same inputs always produce the same key."""
    key1 = generate_idempotency_key(event_id=42, strategy="A", attempt_number=1)
    key2 = generate_idempotency_key(event_id=42, strategy="A", attempt_number=1)
    assert key1 == key2


def test_idempotency_key_varies_by_attempt():
    """Different attempt numbers produce different keys."""
    key1 = generate_idempotency_key(event_id=42, strategy="A", attempt_number=1)
    key2 = generate_idempotency_key(event_id=42, strategy="A", attempt_number=2)
    assert key1 != key2


def test_idempotency_key_varies_by_event():
    """Different event IDs produce different keys."""
    key1 = generate_idempotency_key(event_id=1, strategy="A", attempt_number=1)
    key2 = generate_idempotency_key(event_id=2, strategy="A", attempt_number=1)
    assert key1 != key2


def test_idempotency_key_varies_by_strategy():
    """Different strategies for the same event produce different keys (cross-strategy isolation)."""
    key1 = generate_idempotency_key(event_id=42, strategy="A", attempt_number=1)
    key2 = generate_idempotency_key(event_id=42, strategy="B", attempt_number=1)
    assert key1 != key2


def test_idempotency_key_format():
    """Key follows the expected prefix format."""
    key = generate_idempotency_key(event_id=7, strategy="C", attempt_number=3)
    assert key.startswith("reclaim_C_7_3_")
    assert len(key) > len("reclaim_C_7_3_")


# --- 2. Core Idempotency Enforcement Tests ---

def test_execute_retry_idempotent(db_session, sample_event, sample_decision):
    """
    CRITICAL TEST (GEMINI.md Section 9):
    Calling execute_retry twice with the same (event_id, attempt_number)
    must NOT produce two ActionTaken rows.
    """
    # First execution — should succeed
    was_new_1, action_1 = execute_retry(
        event=sample_event,
        decision=sample_decision,
        attempt_number=1,
        db=db_session
    )
    assert was_new_1 is True
    assert action_1 is not None
    assert action_1.action_type == "retry_now"

    # Second execution with same key — must be deduplicated
    was_new_2, action_2 = execute_retry(
        event=sample_event,
        decision=sample_decision,
        attempt_number=1,
        db=db_session
    )
    assert was_new_2 is False
    assert action_2 is not None
    assert action_2.id == action_1.id  # Same row returned, not a new one

    # Verify only 1 action exists in the database
    all_actions = db_session.query(ActionTaken).all()
    assert len(all_actions) == 1


def test_execute_retry_different_attempts_allowed(db_session, sample_event, sample_decision):
    """Different attempt numbers should each produce a separate action."""
    was_new_1, action_1 = execute_retry(
        sample_event, sample_decision, attempt_number=1, db=db_session
    )
    was_new_2, action_2 = execute_retry(
        sample_event, sample_decision, attempt_number=2, db=db_session
    )

    assert was_new_1 is True
    assert was_new_2 is True
    assert action_1.id != action_2.id
    assert action_1.idempotency_key != action_2.idempotency_key

    all_actions = db_session.query(ActionTaken).all()
    assert len(all_actions) == 2


# --- 3. Stubbed Action Idempotency Tests ---

def test_switch_method_stubbed_and_idempotent(db_session, sample_event):
    """switch_method is stubbed (no real API call) and idempotent."""
    decision = Decision(
        event_id=sample_event.id,
        strategy="B",
        recommended_action="switch_method",
        reasoning="Test: recommending payment method switch"
    )
    db_session.add(decision)
    db_session.commit()

    was_new_1, action_1 = execute_switch_method(
        sample_event, decision, attempt_number=1, db=db_session
    )
    assert was_new_1 is True
    response_data = json.loads(action_1.razorpay_response)
    assert response_data["status"] == "stubbed"
    assert "STUB" in response_data["message"]

    # Duplicate call — must be blocked
    was_new_2, action_2 = execute_switch_method(
        sample_event, decision, attempt_number=1, db=db_session
    )
    assert was_new_2 is False
    assert action_2.id == action_1.id

    assert db_session.query(ActionTaken).count() == 1


def test_escalate_human_stubbed_and_idempotent(db_session, sample_event):
    """escalate_human is stubbed and idempotent."""
    decision = Decision(
        event_id=sample_event.id,
        strategy="C",
        recommended_action="escalate_human",
        reasoning="Test: escalating to human review"
    )
    db_session.add(decision)
    db_session.commit()

    was_new_1, action_1 = execute_escalate_human(
        sample_event, decision, attempt_number=1, db=db_session
    )
    assert was_new_1 is True
    response_data = json.loads(action_1.razorpay_response)
    assert response_data["status"] == "stubbed"
    assert "human review ticket" in response_data["message"]

    was_new_2, _ = execute_escalate_human(
        sample_event, decision, attempt_number=1, db=db_session
    )
    assert was_new_2 is False
    assert db_session.query(ActionTaken).count() == 1


# --- 4. Dispatch Tests ---

def test_execute_action_dispatches_retry(db_session, sample_event, sample_decision):
    """execute_action correctly dispatches retry_now to execute_retry."""
    was_new, action = execute_action(
        sample_event, sample_decision, attempt_number=1, db=db_session
    )
    assert was_new is True
    assert action.action_type == "retry_now"


def test_execute_action_stop_no_action(db_session, sample_event):
    """'stop' action produces no ActionTaken row — by design."""
    decision = Decision(
        event_id=sample_event.id,
        strategy="B",
        recommended_action="stop",
        reasoning="Test: stop action"
    )
    db_session.add(decision)
    db_session.commit()

    was_new, action = execute_action(
        sample_event, decision, attempt_number=1, db=db_session
    )
    assert was_new is False
    assert action is None
    assert db_session.query(ActionTaken).count() == 0


# --- 5. Prior Attempt Counting ---

def test_count_prior_retry_attempts(db_session, sample_event, sample_decision):
    """Counts only retry actions, isolated by strategy, not other action types."""
    assert count_prior_retry_attempts(sample_event.id, "C", db_session) == 0

    execute_retry(sample_event, sample_decision, attempt_number=1, db=db_session)
    assert count_prior_retry_attempts(sample_event.id, "C", db_session) == 1

    execute_retry(sample_event, sample_decision, attempt_number=2, db=db_session)
    assert count_prior_retry_attempts(sample_event.id, "C", db_session) == 2
    
    # Verify Strategy A attempts for the same event are isolated and count as 0
    assert count_prior_retry_attempts(sample_event.id, "A", db_session) == 0

    # switch_method should NOT count as a retry
    switch_decision = Decision(
        event_id=sample_event.id,
        strategy="C",
        recommended_action="switch_method",
        reasoning="Test switch"
    )
    db_session.add(switch_decision)
    db_session.commit()

    execute_switch_method(sample_event, switch_decision, attempt_number=3, db=db_session)
    # Still 2 retries, not 3
    assert count_prior_retry_attempts(sample_event.id, "C", db_session) == 2


# --- 6. Response Content Validation ---

def test_retry_response_contains_expected_fields(db_session, sample_event, sample_decision):
    """Verify the simulated Razorpay response has expected structure."""
    _, action = execute_retry(
        sample_event, sample_decision, attempt_number=1, db=db_session
    )
    response = json.loads(action.razorpay_response)

    assert response["status"] == "executed"
    assert response["payment_id"] == "pay_test_idem_001"
    assert response["amount"] == 1500.0
    assert response["currency"] == "INR"
    assert response["test_mode"] is True
    assert "idempotency_key" in response
