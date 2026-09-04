"""
Deterministic Policy Engine for Reclaim.
SOLE component authorized to approve payment recovery executions.
Contains ZERO LLM calls, strictly enforcing deterministic business and safety constraints.
"""

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, Tuple
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import Decision, PolicyVerdict, PaymentEvent, ActionTaken
from app.root_cause import HARD_DECLINE, RISKY, SOFT_DECLINE, INSUFFICIENT_FUNDS, NETWORK_ERROR, UNKNOWN


class RecoveryActionEnum(str, Enum):
    RETRY_NOW = "retry_now"
    RETRY_LATER = "retry_later"
    SWITCH_METHOD = "switch_method"
    ESCALATE_HUMAN = "escalate_human"
    STOP = "stop"


class RejectionRuleEnum(str, Enum):
    """Structured rejection reason categories, set at verdict creation time."""
    BUCKET_BLOCKED = "BUCKET_BLOCKED"
    AGE_CUTOFF_EXCEEDED = "AGE_CUTOFF_EXCEEDED"
    RETRY_CAP_EXCEEDED = "RETRY_CAP_EXCEEDED"
    BACKOFF_WINDOW_VIOLATED = "BACKOFF_WINDOW_VIOLATED"
    INVALID_ACTION = "INVALID_ACTION"


ALLOWED_ACTION_VALUES = {action.value for action in RecoveryActionEnum}

# Hard Constraints per GEMINI.md Section 7
MAX_RETRY_ATTEMPTS = 3
MIN_BACKOFF_HOURS = 4
MAX_FAILURE_AGE_DAYS = 7


class PolicyVerdictResult(BaseModel):
    allowed: bool
    reason: str
    action: Optional[str] = None
    rejection_rule: Optional[RejectionRuleEnum] = None


class PolicyEngine:
    """
    Deterministic rule-based policy engine.
    Enforces non-negotiable safety guardrails on all recommendations before execution.
    """

    @classmethod
    def evaluate(
        cls,
        recommended_action: str,
        root_cause_bucket: str,
        attempts_used: int = 0,
        last_attempt_at: Optional[datetime] = None,
        first_failed_at: Optional[datetime] = None,
        current_time: Optional[datetime] = None
    ) -> PolicyVerdictResult:
        """
        Evaluates a recommended action against all deterministic policy rules.
        """
        now = current_time or datetime.now(timezone.utc)
        action_clean = (recommended_action or "").strip()

        # Rule 5: Action enum validation
        if action_clean not in ALLOWED_ACTION_VALUES:
            return PolicyVerdictResult(
                allowed=False,
                reason=f"Policy Violation: Unrecognized action '{recommended_action}'. Must be one of {sorted(list(ALLOWED_ACTION_VALUES))}.",
                action=action_clean,
                rejection_rule=RejectionRuleEnum.INVALID_ACTION
            )

        # Rule 1: hard_decline, risky, and unknown buckets can NEVER be auto-retried
        if root_cause_bucket in [HARD_DECLINE, RISKY, UNKNOWN] and action_clean in [RecoveryActionEnum.RETRY_NOW.value, RecoveryActionEnum.RETRY_LATER.value]:
            return PolicyVerdictResult(
                allowed=False,
                reason=f"Policy Violation: Automated retry ('{action_clean}') is strictly prohibited for '{root_cause_bucket}' bucket.",
                action=action_clean,
                rejection_rule=RejectionRuleEnum.BUCKET_BLOCKED
            )

        # Rule 4: No retries after 7 days from first failure
        if first_failed_at and action_clean in [RecoveryActionEnum.RETRY_NOW.value, RecoveryActionEnum.RETRY_LATER.value]:
            # Ensure timezone awareness for subtraction
            if first_failed_at.tzinfo is None:
                first_failed_at = first_failed_at.replace(tzinfo=timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)

            age = now - first_failed_at
            if age > timedelta(days=MAX_FAILURE_AGE_DAYS):
                return PolicyVerdictResult(
                    allowed=False,
                    reason=f"Policy Violation: Payment age is {age.days} days ({age.total_seconds() / 3600:.1f} hours), exceeding {MAX_FAILURE_AGE_DAYS}-day lifetime window.",
                    action=action_clean,
                    rejection_rule=RejectionRuleEnum.AGE_CUTOFF_EXCEEDED
                )

        # Rule 2: Maximum 3 retry attempts per payment lifetime
        if action_clean in [RecoveryActionEnum.RETRY_NOW.value, RecoveryActionEnum.RETRY_LATER.value]:
            if attempts_used >= MAX_RETRY_ATTEMPTS:
                return PolicyVerdictResult(
                    allowed=False,
                    reason=f"Policy Violation: Payment has reached maximum retry attempt limit of {MAX_RETRY_ATTEMPTS} (attempts used: {attempts_used}).",
                    action=action_clean,
                    rejection_rule=RejectionRuleEnum.RETRY_CAP_EXCEEDED
                )

        # Rule 3: Minimum 4-hour gap between retry attempts
        if last_attempt_at and action_clean in [RecoveryActionEnum.RETRY_NOW.value, RecoveryActionEnum.RETRY_LATER.value]:
            if last_attempt_at.tzinfo is None:
                last_attempt_at = last_attempt_at.replace(tzinfo=timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)

            gap = now - last_attempt_at
            if gap < timedelta(hours=MIN_BACKOFF_HOURS):
                return PolicyVerdictResult(
                    allowed=False,
                    reason=f"Policy Violation: Minimum backoff window violated. Only {gap.total_seconds() / 3600:.2f} hours elapsed since last retry (minimum {MIN_BACKOFF_HOURS} hours required).",
                    action=action_clean,
                    rejection_rule=RejectionRuleEnum.BACKOFF_WINDOW_VIOLATED
                )

        # All rules passed - Approval (rejection_rule stays None)
        return PolicyVerdictResult(
            allowed=True,
            reason=f"Policy Approved: Action '{action_clean}' is compliant for root cause '{root_cause_bucket}' (attempts: {attempts_used}/{MAX_RETRY_ATTEMPTS}).",
            action=action_clean
        )

    @classmethod
    def evaluate_and_record(
        cls,
        decision: Decision,
        root_cause_bucket: str,
        event: PaymentEvent,
        db: Session,
        current_time: Optional[datetime] = None
    ) -> PolicyVerdict:
        """
        Evaluates a Decision instance and persists the resulting PolicyVerdict to the database.
        """
        # Count prior retry attempts from database
        prior_actions = db.query(ActionTaken).filter_by(decision_id=decision.id).all()
        # Also count all previous actions taken on this payment event for the current strategy
        all_event_decisions = db.query(Decision).filter_by(event_id=event.id, strategy=decision.strategy).all()
        decision_ids = [d.id for d in all_event_decisions]
        
        all_actions = []
        if decision_ids:
            all_actions = db.query(ActionTaken).filter(
                ActionTaken.decision_id.in_(decision_ids),
                ActionTaken.action_type.in_([RecoveryActionEnum.RETRY_NOW.value, RecoveryActionEnum.RETRY_LATER.value])
            ).all()
        
        attempts_used = len(all_actions)
        last_attempt = max([a.executed_at for a in all_actions], default=None)

        verdict_result = cls.evaluate(
            recommended_action=decision.recommended_action,
            root_cause_bucket=root_cause_bucket,
            attempts_used=attempts_used,
            last_attempt_at=last_attempt,
            first_failed_at=event.created_at,
            current_time=current_time
        )

        verdict = PolicyVerdict(
            decision_id=decision.id,
            allowed=verdict_result.allowed,
            reason=verdict_result.reason,
            rejection_rule=verdict_result.rejection_rule.value if verdict_result.rejection_rule else None
        )
        db.add(verdict)
        db.commit()
        db.refresh(verdict)
        return verdict


def get_policy_rejections(db: Session, strategy: Optional[str] = None) -> list[dict]:
    """
    Surfaces all policy rejections, categorizing each by the structured rejection_rule
    column rather than fragile substring-matching on the free-text reason.
    """
    query = db.query(PolicyVerdict, Decision, PaymentEvent).join(
        Decision, PolicyVerdict.decision_id == Decision.id
    ).join(
        PaymentEvent, Decision.event_id == PaymentEvent.id
    ).filter(PolicyVerdict.allowed == False)

    if strategy:
        query = query.filter(Decision.strategy == strategy)

    results = []
    for verdict, decision, event in query.all():
        # Use the structured rejection_rule column directly — no string-matching.
        category = verdict.rejection_rule or "GENERAL_POLICY_VIOLATION"

        results.append({
            "verdict_id": verdict.id,
            "decision_id": decision.id,
            "event_id": event.id,
            "razorpay_payment_id": event.razorpay_payment_id,
            "strategy": decision.strategy,
            "recommended_action": decision.recommended_action,
            "rejection_category": category,
            "reason": verdict.reason,
            "created_at": verdict.created_at
        })
    return results


def get_age_cutoff_exceptions(db: Session, strategy: Optional[str] = None) -> list[dict]:
    """
    Specifically queries payments blocked by the 7-day age cutoff (Rule 4)
    using the structured rejection_rule column (FR-4.5). Robust against
    reason-string rewording — relies on RejectionRuleEnum.AGE_CUTOFF_EXCEEDED.
    """
    all_rejections = get_policy_rejections(db, strategy=strategy)
    return [r for r in all_rejections if r["rejection_category"] == RejectionRuleEnum.AGE_CUTOFF_EXCEEDED.value]

