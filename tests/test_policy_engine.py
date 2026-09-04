"""
Unit and Integration Tests for Deterministic Policy Engine.
Verifies all safety rules defined in GEMINI.md Section 7:
1. Block auto-retry on hard_decline & risky (even when recommended).
2. Max 3 retry attempts per payment lifetime.
3. Minimum 4-hour backoff window between attempts.
4. Max 7-day age window from initial failure.
5. Strict validation of fixed action enum (invalid strings rejected safely).
6. Human-readable reason strings on all verdicts.
"""

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import PaymentEvent, Decision, PolicyVerdict, ActionTaken
from app.policy_engine import (
    PolicyEngine,
    RecoveryActionEnum,
    RejectionRuleEnum,
    MAX_RETRY_ATTEMPTS,
    MIN_BACKOFF_HOURS,
    MAX_FAILURE_AGE_DAYS
)
from app.root_cause import HARD_DECLINE, RISKY, SOFT_DECLINE, INSUFFICIENT_FUNDS, NETWORK_ERROR, UNKNOWN
from app.policy_engine import get_policy_rejections, get_age_cutoff_exceptions


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


# --- 1. Out-of-Policy Recommendation Tests (Mandatory Safety Test) ---

def test_hard_decline_auto_retry_blocked():
    """
    CRITICAL SAFETY TEST:
    When a recommender suggests 'retry_now' or 'retry_later' on a hard decline,
    the Policy Engine MUST deterministically block execution.
    """
    # Test retry_now on hard_decline
    result_now = PolicyEngine.evaluate(
        recommended_action="retry_now",
        root_cause_bucket=HARD_DECLINE,
        attempts_used=0
    )
    assert result_now.allowed is False
    assert "strictly prohibited for 'hard_decline'" in result_now.reason
    assert result_now.rejection_rule == RejectionRuleEnum.BUCKET_BLOCKED

    # Test retry_later on hard_decline
    result_later = PolicyEngine.evaluate(
        recommended_action="retry_later",
        root_cause_bucket=HARD_DECLINE,
        attempts_used=0
    )
    assert result_later.allowed is False
    assert "strictly prohibited for 'hard_decline'" in result_later.reason
    assert result_later.rejection_rule == RejectionRuleEnum.BUCKET_BLOCKED


def test_risky_auto_retry_blocked():
    """
    CRITICAL SAFETY TEST:
    When a recommender suggests 'retry_now' on a risky payment,
    the Policy Engine MUST deterministically block execution.
    """
    result = PolicyEngine.evaluate(
        recommended_action="retry_now",
        root_cause_bucket=RISKY,
        attempts_used=0
    )
    assert result.allowed is False
    assert "strictly prohibited for 'risky'" in result.reason
    assert result.rejection_rule == RejectionRuleEnum.BUCKET_BLOCKED


def test_unknown_bucket_auto_retry_blocked():
    """
    CRITICAL SAFETY TEST:
    When a recommender suggests 'retry_now' or 'retry_later' on an 'unknown' bucket,
    the Policy Engine MUST deterministically block execution.
    """
    result_now = PolicyEngine.evaluate(
        recommended_action="retry_now",
        root_cause_bucket=UNKNOWN,
        attempts_used=0
    )
    assert result_now.allowed is False
    assert "strictly prohibited for 'unknown'" in result_now.reason
    assert result_now.rejection_rule == RejectionRuleEnum.BUCKET_BLOCKED

    result_later = PolicyEngine.evaluate(
        recommended_action="retry_later",
        root_cause_bucket=UNKNOWN,
        attempts_used=0
    )
    assert result_later.allowed is False
    assert "strictly prohibited for 'unknown'" in result_later.reason
    assert result_later.rejection_rule == RejectionRuleEnum.BUCKET_BLOCKED


# --- 2. Action Enum Validation Tests ---

def test_unrecognized_action_string_rejected_safely():
    """
    Verify that an arbitrary or hallucinated action string from an LLM
    is safely rejected as a policy violation without raising unhandled exceptions.
    """
    invalid_actions = [
        "refund_customer",
        "retry_immediately_please",
        "send_whatsapp_message",
        "bypass_security",
        "",
        "RETRY_NOW_CAPS_INVALID"
    ]

    for invalid_action in invalid_actions:
        result = PolicyEngine.evaluate(
            recommended_action=invalid_action,
            root_cause_bucket=SOFT_DECLINE,
            attempts_used=0
        )
        assert result.allowed is False
        assert "Unrecognized action" in result.reason
        assert result.rejection_rule == RejectionRuleEnum.INVALID_ACTION


# --- 3. Lifetime Retry Cap Tests (Max 3 Attempts) ---

def test_max_retry_cap_enforced():
    """
    Verify that payments cannot exceed 3 lifetime retry attempts.
    """
    # Attempt 0, 1, 2 should be allowed
    for attempts in [0, 1, 2]:
        res = PolicyEngine.evaluate(
            recommended_action="retry_later",
            root_cause_bucket=SOFT_DECLINE,
            attempts_used=attempts
        )
        assert res.allowed is True

    # Attempt 3 (meaning 3 attempts have already been used, attempting 4th) MUST be blocked
    res_blocked = PolicyEngine.evaluate(
        recommended_action="retry_later",
        root_cause_bucket=SOFT_DECLINE,
        attempts_used=3
    )
    assert res_blocked.allowed is False
    assert "maximum retry attempt limit of 3" in res_blocked.reason
    assert res_blocked.rejection_rule == RejectionRuleEnum.RETRY_CAP_EXCEEDED


# --- 4. Minimum Backoff Gap Tests (Min 4 Hours) ---

def test_minimum_backoff_gap_enforced():
    """
    Verify that retries spaced by less than 4 hours are blocked.
    """
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    
    # Last attempt 2 hours ago (< 4 hours) -> BLOCKED
    last_attempt_recent = now - timedelta(hours=2)
    res_recent = PolicyEngine.evaluate(
        recommended_action="retry_now",
        root_cause_bucket=NETWORK_ERROR,
        attempts_used=1,
        last_attempt_at=last_attempt_recent,
        current_time=now
    )
    assert res_recent.allowed is False
    assert "Minimum backoff window violated" in res_recent.reason
    assert res_recent.rejection_rule == RejectionRuleEnum.BACKOFF_WINDOW_VIOLATED

    # Last attempt 5 hours ago (>= 4 hours) -> ALLOWED
    last_attempt_valid = now - timedelta(hours=5)
    res_valid = PolicyEngine.evaluate(
        recommended_action="retry_later",
        root_cause_bucket=SOFT_DECLINE,
        attempts_used=1,
        last_attempt_at=last_attempt_valid,
        current_time=now
    )
    assert res_valid.allowed is True


# --- 5. Maximum Age Limit Tests (Max 7 Days) ---

def test_max_failure_age_limit_enforced():
    """
    Verify that payments older than 7 days from initial failure are blocked.
    """
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Failed 3 days ago (< 7 days) -> ALLOWED
    first_failed_recent = now - timedelta(days=3)
    res_recent = PolicyEngine.evaluate(
        recommended_action="retry_later",
        root_cause_bucket=SOFT_DECLINE,
        first_failed_at=first_failed_recent,
        current_time=now
    )
    assert res_recent.allowed is True

    # Failed 8 days ago (> 7 days) -> BLOCKED
    first_failed_old = now - timedelta(days=8)
    res_old = PolicyEngine.evaluate(
        recommended_action="retry_later",
        root_cause_bucket=SOFT_DECLINE,
        first_failed_at=first_failed_old,
        current_time=now
    )
    assert res_old.allowed is False
    assert "exceeding 7-day lifetime window" in res_old.reason
    assert res_old.rejection_rule == RejectionRuleEnum.AGE_CUTOFF_EXCEEDED


# --- 6. Compliant Recommendations Approved ---

def test_compliant_actions_approved():
    """
    Verify that compliant recommendations across categories are approved.
    """
    # 1. Stop on hard_decline -> Approved
    res_stop = PolicyEngine.evaluate("stop", HARD_DECLINE)
    assert res_stop.allowed is True

    # 2. Escalate human on risky -> Approved
    res_esc = PolicyEngine.evaluate("escalate_human", RISKY)
    assert res_esc.allowed is True

    # 3. Escalate human on unknown -> Approved
    res_esc_unk = PolicyEngine.evaluate("escalate_human", UNKNOWN)
    assert res_esc_unk.allowed is True
    assert res_esc_unk.rejection_rule is None

    # 4. Switch method on hard decline -> Approved
    res_switch = PolicyEngine.evaluate("switch_method", HARD_DECLINE)
    assert res_switch.allowed is True

    # 5. Retry now on network error -> Approved
    res_net = PolicyEngine.evaluate("retry_now", NETWORK_ERROR)
    assert res_net.allowed is True


# --- 7. Full DB Persistence & Audit Verification ---

def test_evaluate_and_record_integration(db_session):
    """
    Verify evaluate_and_record persists PolicyVerdict linked to Decision and PaymentEvent.
    """
    event = PaymentEvent(
        razorpay_payment_id="pay_test_pol_001",
        amount=1500.0,
        currency="INR",
        failure_reason_raw="Card reported stolen",
        failure_reason_code="GATEWAY_ERROR_CARD_STOLEN",
        created_at=datetime.now(timezone.utc)
    )
    db_session.add(event)
    db_session.commit()

    decision = Decision(
        event_id=event.id,
        strategy="C",
        recommended_action="retry_now",  # Unsafe recommendation
        reasoning="LLM hallucinated immediate retry on stolen card"
    )
    db_session.add(decision)
    db_session.commit()

    # Policy engine processes the decision
    verdict = PolicyEngine.evaluate_and_record(
        decision=decision,
        root_cause_bucket=HARD_DECLINE,
        event=event,
        db=db_session
    )

    assert verdict is not None
    assert verdict.decision_id == decision.id
    assert verdict.allowed is False
    assert "strictly prohibited for 'hard_decline'" in verdict.reason

    # Verify queryable in DB
    saved_verdict = db_session.query(PolicyVerdict).filter_by(decision_id=decision.id).first()
    assert saved_verdict.allowed is False


# --- 8. Specific Query Path for 7-Day Age Cutoff Exceptions (FR-4.5) ---

def test_age_cutoff_exception_query(db_session):
    """
    Verify get_age_cutoff_exceptions specifically isolates payments blocked
    by the 7-day age cutoff as a distinct exception category (requirements.md FR-4.5).
    """
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Old event (> 7 days)
    old_event = PaymentEvent(
        razorpay_payment_id="pay_test_old_001",
        amount=2500.0,
        currency="INR",
        failure_reason_raw="Temporary network issue 10 days ago",
        failure_reason_code="GATEWAY_ERROR_NETWORK_FAILURE",
        created_at=now - timedelta(days=10)
    )
    db_session.add(old_event)
    db_session.commit()

    decision = Decision(
        event_id=old_event.id,
        strategy="C",
        recommended_action="retry_now",
        reasoning="Recommending retry on network glitch"
    )
    db_session.add(decision)
    db_session.commit()

    verdict = PolicyEngine.evaluate_and_record(
        decision=decision,
        root_cause_bucket=NETWORK_ERROR,
        event=old_event,
        db=db_session,
        current_time=now
    )

    assert verdict.allowed is False
    assert "exceeding 7-day lifetime window" in verdict.reason

    # Surface exceptions using the dedicated helper
    age_exceptions = get_age_cutoff_exceptions(db_session)
    assert len(age_exceptions) == 1
    assert age_exceptions[0]["razorpay_payment_id"] == "pay_test_old_001"
    assert age_exceptions[0]["rejection_category"] == RejectionRuleEnum.AGE_CUTOFF_EXCEEDED.value
    assert "exceeding 7-day lifetime window" in age_exceptions[0]["reason"]


def test_evaluate_and_record_cross_strategy_isolation(db_session):
    """
    Verifies that attempts made by Strategy A do not burn into Strategy B's retry caps 
    or backoff windows during evaluate_and_record.
    """
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    
    test_event = PaymentEvent(
        razorpay_payment_id="pay_test_cross_strat_001",
        amount=100.0,
        currency="INR",
        failure_reason_raw="Test failure",
        failure_reason_code="TEST_CODE",
        customer_id="cust_001",
        order_id="order_001",
        created_at=now - timedelta(days=1)
    )
    db_session.add(test_event)
    db_session.commit()
    
    # 1. Setup Strategy A with 3 prior attempts (Maxed out)
    for i in range(3):
        dec_a = Decision(
            event_id=test_event.id,
            strategy="A",
            recommended_action="retry_now",
            reasoning=f"Strategy A attempt {i+1}"
        )
        db_session.add(dec_a)
        db_session.commit()
        
        act_a = ActionTaken(
            decision_id=dec_a.id,
            action_type="retry_now",
            idempotency_key=f"strat_A_key_{i}",
            executed_at=now - timedelta(hours=5), # More than 4 hours ago to bypass backoff rule
            razorpay_response='{}'
        )
        db_session.add(act_a)
        db_session.commit()

    # 2. Evaluate a NEW decision from Strategy B
    dec_b = Decision(
        event_id=test_event.id,
        strategy="B",
        recommended_action="retry_now",
        reasoning="Strategy B first attempt"
    )
    db_session.add(dec_b)
    db_session.commit()

    # 3. Verdict for Strategy B should be APPROVED (0 attempts used for B)
    # If the bug existed, it would see 3 attempts from A and reject B.
    verdict_b = PolicyEngine.evaluate_and_record(
        decision=dec_b,
        root_cause_bucket=SOFT_DECLINE,
        event=test_event,
        db=db_session,
        current_time=now
    )
    
    assert verdict_b.allowed is True
    assert verdict_b.rejection_rule is None
    assert "attempts: 0/3" in verdict_b.reason

