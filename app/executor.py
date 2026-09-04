"""
Executor Module for Reclaim Revenue Recovery Engine.
Handles payment retry execution against Razorpay test-mode API.

CRITICAL DESIGN RULES (per GEMINI.md Section 9):
1. Every execution call uses a unique idempotency key derived from (event_id, attempt_number)
   so a retried request never double-executes.
2. Non-payment interventions (switch_method prompts, reminders) are STUBBED as logged lines,
   not real integrations — out of scope for the hackathon timeline.
3. This module is ONLY called after the Policy Engine has approved the action.
   It must NEVER be called directly from an LLM recommendation.
"""

import json
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple

from sqlalchemy.orm import Session

from app.models import Decision, ActionTaken, PaymentEvent
from app.config import settings


def generate_idempotency_key(event_id: int, strategy: str, attempt_number: int) -> str:
    """
    Generates a deterministic, unique idempotency key from (event_id, strategy, attempt_number).
    Uses a hash to keep keys a consistent length while ensuring uniqueness.
    
    The key format is: reclaim_{strategy}_{event_id}_{attempt_number}_{hash_suffix}
    The hash suffix provides collision resistance if IDs are reused across DB resets.
    """
    raw = f"reclaim_v1_{event_id}_{strategy}_{attempt_number}_{settings.RAZORPAY_KEY_ID}"
    hash_suffix = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"reclaim_{strategy}_{event_id}_{attempt_number}_{hash_suffix}"


def execute_retry(
    event: PaymentEvent,
    decision: Decision,
    attempt_number: int,
    db: Session,
    current_time: Optional[datetime] = None
) -> Tuple[bool, Optional[ActionTaken]]:
    """
    Executes a payment retry against Razorpay test-mode API.
    
    Returns: (was_new_execution: bool, action_taken: ActionTaken or None)
    - (True, ActionTaken) if a new execution was performed
    - (False, existing_ActionTaken) if idempotency key already exists (duplicate blocked)
    """
    now = current_time or datetime.now(timezone.utc)
    idempotency_key = generate_idempotency_key(event.id, decision.strategy, attempt_number)

    # Idempotency check: if this key already exists, block the duplicate
    existing = db.query(ActionTaken).filter_by(idempotency_key=idempotency_key).first()
    if existing:
        return False, existing

    # In a real integration, this would call Razorpay's payment retry API:
    #   razorpay_client.payment.fetch(event.razorpay_payment_id)
    #   razorpay_client.payment.capture(...)
    # For test-mode / hackathon, we simulate the API response.
    razorpay_response = json.dumps({
        "status": "executed",
        "action": decision.recommended_action,
        "payment_id": event.razorpay_payment_id,
        "amount": event.amount,
        "currency": event.currency,
        "idempotency_key": idempotency_key,
        "test_mode": True,
        "message": f"Retry attempt {attempt_number} executed in test mode"
    })

    action = ActionTaken(
        decision_id=decision.id,
        action_type=decision.recommended_action,
        idempotency_key=idempotency_key,
        executed_at=now,
        razorpay_response=razorpay_response
    )
    db.add(action)
    db.commit()
    db.refresh(action)

    return True, action


def execute_switch_method(
    event: PaymentEvent,
    decision: Decision,
    attempt_number: int,
    db: Session,
    current_time: Optional[datetime] = None
) -> Tuple[bool, Optional[ActionTaken]]:
    """
    STUB: Logs a 'switch_method' prompt that would be sent to the customer.
    Per GEMINI.md Section 9, notification actions are stubbed as logged lines,
    not real integrations.
    """
    now = current_time or datetime.now(timezone.utc)
    idempotency_key = generate_idempotency_key(event.id, decision.strategy, attempt_number)

    existing = db.query(ActionTaken).filter_by(idempotency_key=idempotency_key).first()
    if existing:
        return False, existing

    # Stub: log what would have been sent
    razorpay_response = json.dumps({
        "status": "stubbed",
        "action": "switch_method",
        "payment_id": event.razorpay_payment_id,
        "amount": event.amount,
        "message": (
            f"STUB: Would send payment method switch prompt to customer "
            f"{event.customer_id} for INR {event.amount:.2f}. "
            f"Original method failed with: {event.failure_reason_code}"
        ),
        "test_mode": True
    })

    action = ActionTaken(
        decision_id=decision.id,
        action_type="switch_method",
        idempotency_key=idempotency_key,
        executed_at=now,
        razorpay_response=razorpay_response
    )
    db.add(action)
    db.commit()
    db.refresh(action)

    return True, action


def execute_escalate_human(
    event: PaymentEvent,
    decision: Decision,
    attempt_number: int,
    db: Session,
    current_time: Optional[datetime] = None
) -> Tuple[bool, Optional[ActionTaken]]:
    """
    STUB: Logs an 'escalate_human' action that would create a support ticket
    or flag for manual review.
    """
    now = current_time or datetime.now(timezone.utc)
    idempotency_key = generate_idempotency_key(event.id, decision.strategy, attempt_number)

    existing = db.query(ActionTaken).filter_by(idempotency_key=idempotency_key).first()
    if existing:
        return False, existing

    razorpay_response = json.dumps({
        "status": "stubbed",
        "action": "escalate_human",
        "payment_id": event.razorpay_payment_id,
        "amount": event.amount,
        "message": (
            f"STUB: Would create human review ticket for payment "
            f"{event.razorpay_payment_id} (INR {event.amount:.2f}). "
            f"Root cause requires manual investigation: {event.failure_reason_code}"
        ),
        "test_mode": True
    })

    action = ActionTaken(
        decision_id=decision.id,
        action_type="escalate_human",
        idempotency_key=idempotency_key,
        executed_at=now,
        razorpay_response=razorpay_response
    )
    db.add(action)
    db.commit()
    db.refresh(action)

    return True, action


def execute_action(
    event: PaymentEvent,
    decision: Decision,
    attempt_number: int,
    db: Session,
    current_time: Optional[datetime] = None
) -> Tuple[bool, Optional[ActionTaken]]:
    """
    Dispatches execution to the appropriate handler based on the approved action type.
    
    This is the single entry point called AFTER the Policy Engine has approved.
    Returns: (was_new_execution, action_taken)
    """
    action_type = decision.recommended_action

    if action_type in ("retry_now", "retry_later"):
        return execute_retry(event, decision, attempt_number, db, current_time)
    elif action_type == "switch_method":
        return execute_switch_method(event, decision, attempt_number, db, current_time)
    elif action_type == "escalate_human":
        return execute_escalate_human(event, decision, attempt_number, db, current_time)
    elif action_type == "stop":
        # 'stop' means cease all activity — no action is recorded, by design.
        return False, None
    else:
        # Assumption: this branch should never be reached if the Policy Engine
        # validated the action enum. If it is reached, log defensively but do not execute.
        return False, None


def count_prior_retry_attempts(event_id: int, strategy: str, db: Session) -> int:
    """
    Counts all prior retry attempts (retry_now or retry_later) for a given payment event
    AND strategy. Used to derive the attempt_number for idempotency key generation
    without cross-strategy interference.
    """
    from app.models import Decision as DecisionModel
    decisions = db.query(DecisionModel).filter_by(event_id=event_id, strategy=strategy).all()
    if not decisions:
        return 0

    decision_ids = [d.id for d in decisions]
    retry_actions = db.query(ActionTaken).filter(
        ActionTaken.decision_id.in_(decision_ids),
        ActionTaken.action_type.in_(["retry_now", "retry_later"])
    ).all()

    return len(retry_actions)
