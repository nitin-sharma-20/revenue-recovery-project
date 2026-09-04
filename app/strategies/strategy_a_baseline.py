"""
Strategy A — Naive Baseline Strategy.
Implements the naive baseline: retry every failed payment once at +24h.
No root cause classification, no branching, no reasoning.
"""

import random
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from app.models import PaymentEvent, Decision, ActionTaken, Outcome


from data.generate_synthetic_data import get_ground_truth_outcome


def simulate_baseline_retry_outcome(event: PaymentEvent) -> tuple[bool, float, int]:
    """
    Simulates execution of a single blind retry for Strategy A using the
    independent ground-truth outcome model pre-computed for this payment.
    Returns: (recovered: bool, amount_recovered: float, attempts_used: int)
    """
    recovered, amount, _ = get_ground_truth_outcome(event.razorpay_payment_id, "retry_now")
    return recovered, amount, 1




def run_strategy_a(events: List[PaymentEvent], db: Session) -> Dict[str, Any]:
    """
    Executes Strategy A across a list of payment events.
    Records decisions, actions taken, and outcomes for every event.
    """
    results = {
        "strategy": "A",
        "total_events": len(events),
        "recovered_count": 0,
        "total_amount_recovered": 0.0,
        "total_attempts": 0,
        "outcomes": []
    }

    for event in events:
        # 1. Fixed naive decision: Blind retry at +24h
        decision = Decision(
            event_id=event.id,
            strategy="A",
            recommended_action="retry_now",  # Executed as standard retry action
            reasoning="Strategy A (Naive Baseline): Blind retry at +24h without root cause classification"
        )
        db.add(decision)
        db.flush()

        # 2. Record action taken
        action = ActionTaken(
            decision_id=decision.id,
            action_type="retry_now",
            idempotency_key=f"strat_a_{event.id}_attempt_1",
            razorpay_response='{"status": "retried_at_24h", "strategy": "A"}'
        )
        db.add(action)

        # 3. Simulate outcome
        recovered, amount_recovered, attempts = simulate_baseline_retry_outcome(event)

        outcome = Outcome(
            event_id=event.id,
            strategy="A",
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
