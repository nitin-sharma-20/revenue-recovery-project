"""
Strategy C — Reclaim (LLM + Policy Engine).
LLM recommends an action + reasoning → Policy Engine validates against rules → executes only if approved.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.models import PaymentEvent, Decision, Outcome
from app.root_cause import classify_and_persist
from app.llm_recommender import get_llm_recommendation
from app.policy_engine import PolicyEngine
from app.executor import execute_action, count_prior_retry_attempts
from data.generate_synthetic_data import get_ground_truth_outcome


def simulate_executor_outcome(event: PaymentEvent, action: str, bucket: str) -> tuple[bool, float, int]:
    """
    Simulates recovery outcome for Strategy C based on the executed action and the
    independent ground-truth outcome model pre-computed for this payment.
    Returns: (recovered: bool, amount_recovered: float, attempts_used: int)
    """
    if action in ["stop", "escalate_human"]:
        return False, 0.0, 0

    return get_ground_truth_outcome(event.razorpay_payment_id, action)


def run_strategy_c(events: List[PaymentEvent], db: Session) -> Dict[str, Any]:
    """
    Executes Strategy C across a list of payment events.
    Records root cause classifications, decisions, policy verdicts, actions taken, and outcomes.
    """
    results = {
        "strategy": "C",
        "total_events": len(events),
        "recovered_count": 0,
        "total_amount_recovered": 0.0,
        "total_attempts": 0,
        "llm_decisions": 0,
        "fallback_decisions": 0,
        "outcomes": []
    }
    now = datetime.now(timezone.utc)

    for event in events:
        # 1. Root Cause Classification
        classification = classify_and_persist(event, db)
        bucket = classification.bucket

        # Determine prior attempts for LLM context (isolated to Strategy C)
        prior_attempts = count_prior_retry_attempts(event.id, "C", db)

        # 2. LLM Recommendation
        recommendation, source = get_llm_recommendation(
            root_cause_bucket=bucket,
            amount=event.amount,
            error_code=event.failure_reason_code,
            error_description=event.failure_reason_raw,
            previous_attempts=prior_attempts,
            created_at=event.created_at,
            current_time=now
        )
        
        if source.startswith("llm"):
            results["llm_decisions"] += 1
        else:
            results["fallback_decisions"] += 1

        source_label = "LLM" if source.startswith("llm") else "FALLBACK"
        decision = Decision(
            event_id=event.id,
            strategy="C",
            recommended_action=recommendation.action.value,
            reasoning=f"[{source_label}]: {recommendation.reasoning}"
        )
        db.add(decision)
        db.flush()

        # 3. Policy Verdict (Deterministic Policy Check)
        verdict = PolicyEngine.evaluate_and_record(
            decision=decision,
            root_cause_bucket=bucket,
            event=event,
            db=db,
            current_time=now
        )

        recovered, amount_recovered, attempts_used_for_outcome = False, 0.0, 0

        # 4. Action Taken (only if approved)
        if verdict.allowed:
            # We must pass the correct attempt number to the executor for idempotency.
            # Strategy C's next attempt number is prior_attempts + 1
            attempt_number = prior_attempts + 1
            
            was_new_execution, action_taken = execute_action(
                event=event,
                decision=decision,
                attempt_number=attempt_number,
                db=db,
                current_time=now
            )
            
            if action_taken:
                # 5. Outcome Recording
                recovered, amount_recovered, attempts_used_for_outcome = simulate_executor_outcome(
                    event, action_taken.action_type, bucket
                )

        outcome = Outcome(
            event_id=event.id,
            strategy="C",
            recovered=recovered,
            amount_recovered=amount_recovered,
            attempts_used=attempts_used_for_outcome
        )
        db.add(outcome)
        db.flush()

        results["total_attempts"] += attempts_used_for_outcome
        if recovered:
            results["recovered_count"] += 1
            results["total_amount_recovered"] += amount_recovered

        results["outcomes"].append({
            "event_id": event.id,
            "razorpay_payment_id": event.razorpay_payment_id,
            "bucket": bucket,
            "action": decision.recommended_action,
            "verdict_allowed": verdict.allowed,
            "rejection_rule": verdict.rejection_rule,
            "recovered": recovered,
            "amount_recovered": amount_recovered,
            "attempts_used": attempts_used_for_outcome
        })

    db.commit()
    results["total_amount_recovered"] = round(results["total_amount_recovered"], 2)
    results["recovery_rate"] = round(results["recovered_count"] / len(events) * 100, 2) if events else 0.0
    results["recovery_per_intervention"] = round(
        results["total_amount_recovered"] / results["total_attempts"], 2
    ) if results["total_attempts"] > 0 else 0.0

    return results
