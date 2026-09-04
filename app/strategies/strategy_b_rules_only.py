"""
Strategy B — Rule-Only Policy Strategy.
Implements deterministic root-cause → action mapping, retry caps, and backoff.
No LLM involved. All rules and policy checks are strictly deterministic.
"""

import random
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from app.models import PaymentEvent, Decision, PolicyVerdict, ActionTaken, Outcome
from app.root_cause import (
    classify_failure_by_rule,
    classify_and_persist,
    HARD_DECLINE,
    SOFT_DECLINE,
    INSUFFICIENT_FUNDS,
    NETWORK_ERROR,
    RISKY,
    UNKNOWN
)


def map_root_cause_to_action(bucket: str) -> tuple[str, str]:
    """
    Deterministic rule mapping from root-cause bucket to recommended action.
    Returns: (recommended_action, reasoning)
    """
    if bucket == HARD_DECLINE:
        return "stop", "Rule: Permanent card or account invalidity (hard decline). Auto-retries blocked to prevent merchant fees."
    elif bucket in [RISKY, UNKNOWN]:
        return "escalate_human", f"Rule: '{bucket}' category flagged. Escalate for human verification; do not auto-retry."
    elif bucket == NETWORK_ERROR:
        return "retry_now", "Rule: Transient network timeout/failure detected. Safe to retry immediately."
    elif bucket == INSUFFICIENT_FUNDS:
        return "retry_later", "Rule: Account balance insufficient. Schedule delayed retry with exponential backoff for customer replenishment."
    elif bucket == SOFT_DECLINE:
        return "retry_later", "Rule: Temporary issuer decline. Schedule retry with backoff window."
    else:
        return "stop", f"Rule: Unrecognized bucket '{bucket}'. Default safe stop."



from data.generate_synthetic_data import get_ground_truth_outcome


def simulate_rules_outcome(event: PaymentEvent, action: str, bucket: str) -> tuple[bool, float, int]:
    """
    Simulates recovery outcome for Strategy B based on the chosen action and the
    independent ground-truth outcome model pre-computed for this payment.
    Returns: (recovered: bool, amount_recovered: float, attempts_used: int)
    """
    if action in ["stop", "escalate_human"]:
        return False, 0.0, 0

    return get_ground_truth_outcome(event.razorpay_payment_id, action)



def run_strategy_b(events: List[PaymentEvent], db: Session) -> Dict[str, Any]:
    """
    Executes Strategy B across a list of payment events.
    Records root cause classifications, decisions, policy verdicts, actions taken, and outcomes.
    """
    results = {
        "strategy": "B",
        "total_events": len(events),
        "recovered_count": 0,
        "total_amount_recovered": 0.0,
        "total_attempts": 0,
        "outcomes": []
    }

    for event in events:
        # 1. Root Cause Classification
        classification = classify_and_persist(event, db)
        bucket = classification.bucket

        # 2. Deterministic Rule Recommendation
        recommended_action, reasoning = map_root_cause_to_action(bucket)

        decision = Decision(
            event_id=event.id,
            strategy="B",
            recommended_action=recommended_action,
            reasoning=f"Strategy B (Rules-Only): {reasoning}"
        )
        db.add(decision)
        db.flush()

        # 3. Policy Verdict (Deterministic Policy Check)
        # Verify hard constraints: hard_decline, risky, and unknown cannot auto-retry
        if bucket in [HARD_DECLINE, RISKY, UNKNOWN] and recommended_action in ["retry_now", "retry_later"]:
            verdict_allowed = False
            verdict_reason = f"Policy violation: Auto-retry prohibited for {bucket}"
        else:
            verdict_allowed = True
            verdict_reason = f"Policy approved: Action '{recommended_action}' is valid for bucket '{bucket}'"

        verdict = PolicyVerdict(
            decision_id=decision.id,
            allowed=verdict_allowed,
            reason=verdict_reason
        )
        db.add(verdict)
        db.flush()

        # 4. Action Taken (only if approved and not a pure no-op)
        if verdict_allowed and recommended_action != "stop":
            action = ActionTaken(
                decision_id=decision.id,
                action_type=recommended_action,
                idempotency_key=f"strat_b_{event.id}_{recommended_action}",
                razorpay_response=f'{{"status": "executed", "action": "{recommended_action}", "strategy": "B"}}'
            )
            db.add(action)

        # 5. Outcome Recording
        if verdict_allowed:
            recovered, amount_recovered, attempts = simulate_rules_outcome(event, recommended_action, bucket)
        else:
            recovered, amount_recovered, attempts = False, 0.0, 0

        outcome = Outcome(
            event_id=event.id,
            strategy="B",
            recovered=recovered,
            amount_recovered=amount_recovered,
            attempts_used=attempts
        )
        db.add(outcome)
        db.flush()

        results["total_attempts"] += attempts
        if recovered:
            results["recovered_count"] += 1
            results["total_amount_recovered"] += amount_recovered

        results["outcomes"].append({
            "event_id": event.id,
            "razorpay_payment_id": event.razorpay_payment_id,
            "bucket": bucket,
            "action": recommended_action,
            "recovered": recovered,
            "amount_recovered": amount_recovered,
            "attempts_used": attempts
        })

    db.commit()
    results["total_amount_recovered"] = round(results["total_amount_recovered"], 2)
    results["recovery_rate"] = round(results["recovered_count"] / len(events) * 100, 2) if events else 0.0
    results["recovery_per_intervention"] = round(
        results["total_amount_recovered"] / results["total_attempts"], 2
    ) if results["total_attempts"] > 0 else 0.0

    return results
