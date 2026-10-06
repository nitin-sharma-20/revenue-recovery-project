"""
Audit Trail and Policy Exception Reporting Helpers.

v2 (Rebuild): adds classify_outcome_reason() as the canonical, single-definition
function for outcome reason categorization (DESIGN_DECISIONS.md item 9).
This function is the ONLY place this classification logic lives — the CI test
in tests/test_import_boundary.py asserts exactly one definition exists.

Reconstructs the full end-to-end trace of any payment event:
Event -> Root Cause -> Decision -> Policy Verdict -> Action Taken -> Outcome.
"""

from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from app.models import PaymentEvent, RootCauseClassification, Decision, PolicyVerdict, ActionTaken, Outcome
from app.policy_engine import get_policy_rejections, get_age_cutoff_exceptions, RejectionRuleEnum


def classify_outcome_reason(
    outcome: "Outcome",
    verdict: Optional["PolicyVerdict"],
    event: "PaymentEvent",
    root_cause_bucket: str,
) -> str:
    """
    THE single, canonical function for classifying why an outcome has the result it has.

    DESIGN_DECISIONS.md item 9: this is imported by eval/run_evaluation.py,
    eval/view_audit_trail.py, and any report generator. There is NO second
    implementation of this logic anywhere. The CI test in tests/test_import_boundary.py
    enforces that exactly one definition exists.

    Returns one of:
      "recovered"             -- outcome.recovered is True
      "instrument_ineligible" -- verdict blocked on INSTRUMENT_INELIGIBLE specifically
      "policy_blocked"        -- verdict exists and allowed=False (any other rule)
      "not_attempted_stop"    -- action was stop or escalate_human; no retry attempted
      "attempted_and_failed"  -- verdict allowed, action taken, but not recovered
    """
    if outcome.recovered:
        return "recovered"

    if verdict is not None and not verdict.allowed:
        if verdict.rejection_rule == RejectionRuleEnum.INSTRUMENT_INELIGIBLE.value:
            return "instrument_ineligible"
        return "policy_blocked"

    # Verdict was allowed (or no verdict, e.g. baseline_a which skips policy check)
    # Check if the action was a deliberate non-attempt
    # We infer from the decision's recommended_action when no verdict exists
    action = None
    if verdict is not None and verdict.decision is not None:
        action = verdict.decision.recommended_action
    elif outcome.event is not None:
        # Fall back: find the decision for this outcome's condition/run
        # (outcome.event relationship must be loaded)
        pass

    if action in ("stop", "escalate_human"):
        return "not_attempted_stop"

    if outcome.attempts_used == 0:
        return "not_attempted_stop"

    return "attempted_and_failed"


def get_event_audit_trail(event_id: int, db: Session) -> Optional[Dict[str, Any]]:
    """
    Reconstructs the complete lifecycle and chronological audit trail for a single payment event.
    """
    event = db.query(PaymentEvent).filter_by(id=event_id).first()
    if not event:
        return None

    classifications = db.query(RootCauseClassification).filter_by(event_id=event.id).all()
    decisions = db.query(Decision).filter_by(event_id=event.id).all()
    outcomes = db.query(Outcome).filter_by(event_id=event.id).all()

    decision_traces = []
    for d in decisions:
        verdict = db.query(PolicyVerdict).filter_by(decision_id=d.id).first()
        actions = db.query(ActionTaken).filter_by(decision_id=d.id).all()

        decision_traces.append({
            "decision_id": d.id,
            "strategy": d.strategy,
            "recommended_action": d.recommended_action,
            "reasoning": d.reasoning,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "verdict": {
                "allowed": verdict.allowed,
                "reason": verdict.reason,
                "rejection_rule": verdict.rejection_rule,
                "created_at": verdict.created_at.isoformat() if verdict.created_at else None
            } if verdict else None,
            "actions_taken": [
                {
                    "action_type": a.action_type,
                    "idempotency_key": a.idempotency_key,
                    "executed_at": a.executed_at.isoformat() if a.executed_at else None,
                    "razorpay_response": a.razorpay_response
                } for a in actions
            ]
        })

    return {
        "event_id": event.id,
        "razorpay_payment_id": event.razorpay_payment_id,
        "amount": event.amount,
        "currency": event.currency,
        "failure_reason_code": event.failure_reason_code,
        "failure_reason_raw": event.failure_reason_raw,
        "customer_id": event.customer_id,
        "order_id": event.order_id,
        "split_bucket": event.split_bucket,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "classifications": [
            {
                "bucket": c.bucket,
                "classified_by": c.classified_by,
                "created_at": c.created_at.isoformat() if c.created_at else None
            } for c in classifications
        ],
        "decisions": decision_traces,
        "outcomes": [
            {
                "strategy": o.strategy,
                "recovered": o.recovered,
                "amount_recovered": o.amount_recovered,
                "attempts_used": o.attempts_used,
                "created_at": o.created_at.isoformat() if o.created_at else None
            } for o in outcomes
        ]
    }


def get_all_exceptions_summary(db: Session, strategy: Optional[str] = None) -> Dict[str, Any]:
    """
    Generates a structured exception list grouped by RejectionRuleEnum category.
    Surfaces payments blocked by the 7-day age cutoff (FR-4.5), retry caps, unsafe buckets, etc.
    """
    rejections = get_policy_rejections(db, strategy=strategy)
    grouped: Dict[str, List[Dict[str, Any]]] = {
        RejectionRuleEnum.AGE_CUTOFF_EXCEEDED.value: [],
        RejectionRuleEnum.BUCKET_BLOCKED.value: [],
        RejectionRuleEnum.RETRY_CAP_EXCEEDED.value: [],
        RejectionRuleEnum.BACKOFF_WINDOW_VIOLATED.value: [],
        RejectionRuleEnum.INVALID_ACTION.value: [],
        "GENERAL_POLICY_VIOLATION": []
    }

    for r in rejections:
        cat = r.get("rejection_category", "GENERAL_POLICY_VIOLATION")
        if cat in grouped:
            grouped[cat].append(r)
        else:
            grouped["GENERAL_POLICY_VIOLATION"].append(r)

    return {
        "total_exceptions": len(rejections),
        "by_category_counts": {k: len(v) for k, v in grouped.items()},
        "exceptions_by_category": grouped,
        "age_cutoff_exceptions": grouped[RejectionRuleEnum.AGE_CUTOFF_EXCEEDED.value]
    }
