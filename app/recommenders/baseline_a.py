"""
app/recommenders/baseline_a.py — Naive baseline recommender (Strategy A comparator).

Rule: retry every failed payment once, unconditionally, without any root-cause
classification. This is the paper's naive baseline — it exists to show what happens
with zero intelligence applied.

ARCHITECTURE RULE: This file MUST NOT import from app.policy_engine or app.executor.
It returns (action, reasoning, source) and nothing else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models import PaymentEvent


def make_baseline_a_recommender():
    """
    Factory returning the naive baseline recommender callable.

    Returns: RecommenderFn = Callable[(PaymentEvent, Session), (str, str, str)]
    """
    def recommender(event: "PaymentEvent", db: "Session") -> tuple[str, str, str]:
        from app.models import Decision, ActionTaken
        
        # Naive baseline: Retry every failed payment exactly ONCE.
        previous_attempts = db.query(ActionTaken).join(Decision).filter(
            Decision.event_id == event.id,
            Decision.condition == "baseline_a",
            ActionTaken.action_type.in_(["retry_now", "retry_later"])
        ).count()

        if previous_attempts == 0:
            return "retry_now", "Naive baseline A: unconditional first retry.", "heuristic"
        else:
            return "stop", "Naive baseline A: already retried once.", "heuristic"

    return recommender
