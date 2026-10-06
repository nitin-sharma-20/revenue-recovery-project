"""
app/pipeline.py — THE single execution pipeline for Reclaim.

ARCHITECTURE RULE (from DESIGN_DECISIONS.md item 1):
  This is the ONLY file that calls PolicyEngine and the Executor.
  Recommenders (flag_off, flag_on, baseline_a) are callables injected here.
  If any recommender file imports PolicyEngine or Executor directly, that is an
  architecture violation — stop and flag it in DESIGN_DECISIONS.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models import PaymentEvent
    from app.recommenders.protocol import LLMClientProtocol

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """
    Outcome of a single evaluate_and_execute() call.
    One of these is produced per (event, condition, run_id).
    """
    event_id: int
    condition: str
    run_id: str
    recommended_action: str
    recommendation_source: str
    reasoning: str
    verdict_allowed: bool
    rejection_rule: Optional[str]
    recovered: bool
    amount_recovered: float
    attempts_used: int


# RecommenderFn signature:
#   (event, db) -> (recommended_action: str, reasoning: str, source_label: str)
RecommenderFn = Callable[["PaymentEvent", "Session"], tuple[str, str, str]]


def evaluate_and_execute(
    event: "PaymentEvent",
    recommender: RecommenderFn,
    condition_label: str,
    run_id: str,
    db: "Session",
    current_time: Optional[datetime] = None,
) -> PipelineResult:
    """
    THE single pipeline entry point.

    Steps (always in this order, never reordered per condition):
      1. Root-cause classification (classify_and_persist)
      2. Recommender callable -> (action, reasoning, source)
      3. Policy Engine -> verdict (persisted)
      4. Executor -> action taken (only if verdict.allowed)
      5. Ground-truth outcome lookup
      6. Outcome row written to DB

    Args:
        event:           PaymentEvent ORM row already in session.
        recommender:     Callable (event, db) -> (action, reasoning, source).
                         Injected per-condition; MUST NOT call PolicyEngine or Executor.
        condition_label: "flag_off" / "flag_on" / "baseline_a"
        run_id:          UUID4 string for this evaluation run.
        db:              SQLAlchemy session (caller owns commit lifecycle).
        current_time:    Override for simulated time (defaults to utcnow).

    Returns:
        PipelineResult with full outcome details.
    """
    from app.root_cause import classify_and_persist
    from app.models import Decision, Outcome
    from app.policy_engine import PolicyEngine
    from data.ground_truth import get_ground_truth_outcome
    from app.recommenders.protocol import LLMProviderExhausted
    
    # 1. Root-cause classification
    root_cause = classify_and_persist(event, db)
    
    # 2. Recommender callable -> (action, reasoning, source)
    try:
        recommended_action, reasoning, source = recommender(event, db)
    except LLMProviderExhausted:
        # Fallback path if LLM fails (DESIGN_DECISIONS.md item 10)
        logger.critical(f"llm_all_providers_failed event_id={event.id}")
        recommended_action = "escalate_human"
        reasoning = "Fallback: All configured LLM providers failed or timed out."
        source = "llm_fallback_all_failed"

    # Persist Decision
    decision = Decision(
        event_id=event.id,
        condition=condition_label,
        run_id=run_id,
        recommended_action=recommended_action,
        reasoning=reasoning,
        recommendation_source=source
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)

    # 3. Policy Engine -> verdict (persisted)
    verdict = PolicyEngine.evaluate_and_record(
        decision=decision,
        root_cause_bucket=root_cause.bucket,
        event=event,
        db=db,
        current_time=current_time
    )

    # 4. Executor -> action taken (only if verdict.allowed)
    if verdict.allowed:
        from app.executor import execute_action
        from app.models import ActionTaken
        from app.policy_engine import RecoveryActionEnum
        
        all_event_decisions = db.query(Decision).filter_by(
            event_id=event.id,
            condition=condition_label,
            run_id=run_id
        ).all()
        decision_ids = [d.id for d in all_event_decisions]
        
        all_actions = []
        if decision_ids:
            all_actions = db.query(ActionTaken).filter(
                ActionTaken.decision_id.in_(decision_ids),
                ActionTaken.action_type.in_([RecoveryActionEnum.RETRY_NOW.value, RecoveryActionEnum.RETRY_LATER.value])
            ).all()
        attempt_num = len(all_actions) + 1
        
        execute_action(
            event=event,
            decision=decision,
            attempt_number=attempt_num,
            db=db,
            current_time=current_time
        )
        
    # 5. Ground-truth outcome lookup (if action taken)
    # The action that actually matters is the one taken (if allowed). If blocked, it's essentially "stop"
    final_action = recommended_action if verdict.allowed else "stop"
    
    recovered, amount_recovered, attempts_used = get_ground_truth_outcome(
        payment_id=event.id,
        action=final_action
    )

    # 6. Outcome row written to DB
    outcome = Outcome(
        event_id=event.id,
        condition=condition_label,
        run_id=run_id,
        recovered=recovered,
        amount_recovered=amount_recovered,
        attempts_used=attempts_used
    )
    db.add(outcome)
    db.commit()
    
    return PipelineResult(
        event_id=event.id,
        condition=condition_label,
        run_id=run_id,
        recommended_action=recommended_action,
        recommendation_source=source,
        reasoning=reasoning,
        verdict_allowed=verdict.allowed,
        rejection_rule=verdict.rejection_rule,
        recovered=recovered,
        amount_recovered=amount_recovered,
        attempts_used=attempts_used
    )
