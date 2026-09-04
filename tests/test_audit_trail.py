"""
End-to-end integration test for Phase 4: Executor + Audit Trail.
Demonstrates a single payment's full lifecycle visible in the database:
Event -> Root Cause -> Decision -> Policy Verdict -> Action Taken -> Outcome.
"""

import json
from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import PaymentEvent, Decision, PolicyVerdict, ActionTaken, Outcome, RootCauseClassification
from app.root_cause import classify_failure_by_rule, NETWORK_ERROR, HARD_DECLINE
from app.policy_engine import PolicyEngine, RejectionRuleEnum
from app.executor import execute_action, count_prior_retry_attempts
from app.audit import get_event_audit_trail


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


def test_full_lifecycle_network_error_approved(db_session):
    """
    End-to-end: a network_error payment goes through classification -> recommendation ->
    policy approval -> execution -> outcome. The full audit trail is reconstructable.
    """
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Payment Event
    event = PaymentEvent(
        razorpay_payment_id="pay_lifecycle_001",
        amount=2500.0,
        currency="INR",
        failure_reason_raw="Connection to bank gateway timed out after 30000ms.",
        failure_reason_code="GATEWAY_ERROR_PAYMENT_TIMED_OUT",
        customer_id="cust_lifecycle_001",
        order_id="order_lifecycle_001",
        split_bucket="dev",
        created_at=now
    )
    db_session.add(event)
    db_session.commit()

    # 2. Root Cause Classification
    bucket, classified_by, reasoning = classify_failure_by_rule(
        event.failure_reason_code, event.failure_reason_raw
    )
    assert bucket == NETWORK_ERROR
    classification = RootCauseClassification(
        event_id=event.id, bucket=bucket, classified_by=classified_by
    )
    db_session.add(classification)
    db_session.commit()

    # 3. Decision (simulating Strategy C recommendation)
    decision = Decision(
        event_id=event.id,
        strategy="C",
        recommended_action="retry_now",
        reasoning="LLM: Transient network timeout. Immediate retry recommended."
    )
    db_session.add(decision)
    db_session.commit()

    # 4. Policy Verdict
    verdict = PolicyEngine.evaluate_and_record(
        decision=decision,
        root_cause_bucket=bucket,
        event=event,
        db=db_session,
        current_time=now
    )
    assert verdict.allowed is True
    assert verdict.rejection_rule is None  # Approved -> no rejection rule

    # 5. Execution (only because policy approved)
    attempt_number = count_prior_retry_attempts(event.id, decision.strategy, db_session) + 1
    was_new, action = execute_action(
        event=event,
        decision=decision,
        attempt_number=attempt_number,
        db=db_session,
        current_time=now
    )
    assert was_new is True
    assert action is not None
    assert action.action_type == "retry_now"

    # 6. Outcome
    outcome = Outcome(
        event_id=event.id,
        strategy="C",
        recovered=True,
        amount_recovered=2500.0,
        attempts_used=1
    )
    db_session.add(outcome)
    db_session.commit()

    # 7. Verify complete audit trail
    trail = get_event_audit_trail(event.id, db_session)
    assert trail is not None
    assert trail["razorpay_payment_id"] == "pay_lifecycle_001"
    assert trail["amount"] == 2500.0

    # Classification present
    assert len(trail["classifications"]) == 1
    assert trail["classifications"][0]["bucket"] == "network_error"

    # Decision chain present
    assert len(trail["decisions"]) == 1
    dec = trail["decisions"][0]
    assert dec["strategy"] == "C"
    assert dec["recommended_action"] == "retry_now"

    # Verdict present and approved
    assert dec["verdict"]["allowed"] is True
    assert dec["verdict"]["rejection_rule"] is None

    # Action taken present
    assert len(dec["actions_taken"]) == 1
    assert dec["actions_taken"][0]["action_type"] == "retry_now"
    assert "idempotency_key" in dec["actions_taken"][0]

    # Outcome present
    assert len(trail["outcomes"]) == 1
    assert trail["outcomes"][0]["recovered"] is True
    assert trail["outcomes"][0]["amount_recovered"] == 2500.0

    # Print the full trail for demo visibility
    print("\n=== FULL AUDIT TRAIL (pay_lifecycle_001) ===")
    print(json.dumps(trail, indent=2, default=str))


def test_full_lifecycle_hard_decline_blocked(db_session):
    """
    End-to-end: a hard_decline payment gets classified -> LLM recommends retry_now ->
    Policy Engine BLOCKS -> no execution -> outcome is unrecovered.
    Verifies the audit trail shows the rejection with structured rejection_rule.
    """
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

    event = PaymentEvent(
        razorpay_payment_id="pay_lifecycle_002",
        amount=5000.0,
        currency="INR",
        failure_reason_raw="Card reported as lost or stolen by cardholder.",
        failure_reason_code="GATEWAY_ERROR_CARD_STOLEN",
        customer_id="cust_lifecycle_002",
        order_id="order_lifecycle_002",
        split_bucket="dev",
        created_at=now
    )
    db_session.add(event)
    db_session.commit()

    # Classify
    bucket, classified_by, _ = classify_failure_by_rule(
        event.failure_reason_code, event.failure_reason_raw
    )
    assert bucket == HARD_DECLINE
    classification = RootCauseClassification(
        event_id=event.id, bucket=bucket, classified_by=classified_by
    )
    db_session.add(classification)
    db_session.commit()

    # LLM hallucinates retry
    decision = Decision(
        event_id=event.id,
        strategy="C",
        recommended_action="retry_now",
        reasoning="LLM hallucinated: retry on stolen card"
    )
    db_session.add(decision)
    db_session.commit()

    # Policy Engine blocks it
    verdict = PolicyEngine.evaluate_and_record(
        decision=decision,
        root_cause_bucket=bucket,
        event=event,
        db=db_session,
        current_time=now
    )
    assert verdict.allowed is False
    assert verdict.rejection_rule == RejectionRuleEnum.BUCKET_BLOCKED.value

    # NO execution — policy blocked
    # Record outcome as unrecovered
    outcome = Outcome(
        event_id=event.id,
        strategy="C",
        recovered=False,
        amount_recovered=0.0,
        attempts_used=0
    )
    db_session.add(outcome)
    db_session.commit()

    # Verify audit trail shows the blocked verdict
    trail = get_event_audit_trail(event.id, db_session)
    assert trail is not None
    dec = trail["decisions"][0]
    assert dec["verdict"]["allowed"] is False
    assert dec["verdict"]["rejection_rule"] == "BUCKET_BLOCKED"
    assert len(dec["actions_taken"]) == 0  # No action taken
    assert trail["outcomes"][0]["recovered"] is False

    print("\n=== FULL AUDIT TRAIL (pay_lifecycle_002 — BLOCKED) ===")
    print(json.dumps(trail, indent=2, default=str))
