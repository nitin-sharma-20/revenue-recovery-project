"""
app/recommenders/rule_based.py — Deterministic rule-based recommender.

Shared by BOTH ablation conditions (flag_off and flag_on) for all five known buckets:
  hard_decline, soft_decline, insufficient_funds, network_error, risky

For the 'unknown' bucket:
  - flag_off: goes straight to escalate_human (this file handles it)
  - flag_on:  calls LLM triage first (handled in llm_triage.py, which wraps this)

ARCHITECTURE RULE: This file MUST NOT import from app.policy_engine or app.executor.
It returns (action, reasoning, source) and nothing else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models import PaymentEvent


# Root cause bucket constants (mirrored from app.root_cause to avoid circular import)
HARD_DECLINE = "hard_decline"
SOFT_DECLINE = "soft_decline"
INSUFFICIENT_FUNDS = "insufficient_funds"
NETWORK_ERROR = "network_error"
RISKY = "risky"
UNKNOWN = "unknown"


def rule_based_recommend(
    bucket: str,
    amount: float,
    previous_attempts: int,
) -> tuple[str, str]:
    """
    Deterministic root-cause -> action mapping.
    Used identically by flag_off and flag_on for the five known buckets.

    Returns: (recommended_action, reasoning)
    """
    if bucket == HARD_DECLINE:
        return "stop", "Hard decline cannot be recovered."
    elif bucket == RISKY:
        return "escalate_human", "Risky transactions require human review."
    elif bucket == NETWORK_ERROR:
        return "retry_now", "Network errors are transient, safe to retry immediately."
    elif bucket == INSUFFICIENT_FUNDS:
        return "retry_later", "Insufficient funds need time for customer to top up."
    elif bucket == SOFT_DECLINE:
        if previous_attempts == 0:
            return "retry_now", "First soft decline can be retried immediately."
        else:
            return "retry_later", "Subsequent soft declines need backoff."
    elif bucket == UNKNOWN:
        return "escalate_human", "Unknown bucket requires human escalation."
    
    # Fallback
    return "escalate_human", "Unrecognized bucket."


def make_ablation_recommender(
    llm_triage_enabled: bool,
    condition_label: str,
    run_id: str,
    llm_client=None,  # LLMClientProtocol | None
):
    """
    Factory returning the ablation recommender callable.

    When llm_triage_enabled=False (flag_off):
      - All buckets use rule_based_recommend().

    When llm_triage_enabled=True (flag_on):
      - known buckets: rule_based_recommend() (identical)
      - unknown bucket: LLM triage via llm_client (see llm_triage.py)
      - LLMProviderExhausted -> caught in pipeline.py, NOT here.
    """
    from app.recommenders.llm_triage import triage_unknown_event
    from app.root_cause import get_event_bucket

    def recommender(event: "PaymentEvent", db: "Session") -> tuple[str, str, str]:
        # Count prior attempts for this event/condition (for rule logic)
        from app.models import Decision, ActionTaken
        
        # NOTE: pipeline expects (action, reasoning, source)
        bucket = get_event_bucket(event, db)
        
        # Count attempts using strict isolation (condition + run_id)
        previous_attempts = db.query(ActionTaken).join(Decision).filter(
            Decision.event_id == event.id,
            Decision.condition == condition_label,
            Decision.run_id == run_id,
            ActionTaken.action_type.in_(["retry_now", "retry_later"])
        ).count()
        
        if bucket == UNKNOWN and llm_triage_enabled:
            if not llm_client:
                raise ValueError("llm_triage_enabled is True but no llm_client provided")
            
            # Use LLM triage
            action, reasoning, source = triage_unknown_event(
                event=event,
                llm_client=llm_client,
                previous_attempts=previous_attempts,
                hours_since_failure=0.0 # Approximation, can be calculated
            )
            return action, reasoning, source
        
        # Use deterministic rule
        action, reasoning = rule_based_recommend(bucket, event.amount, previous_attempts)
        source = "heuristic"
        return action, reasoning, source

    return recommender
