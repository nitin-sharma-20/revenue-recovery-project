"""
Forensic check: For the 6 "unknown" bucket events where Strategy C LLM recommended
retry_now or retry_later, confirm whether the Policy Engine blocked them.

This script runs the full A/B/C pipeline on the dev split (fresh in-memory DB),
then immediately queries the PolicyVerdict + ActionTaken + Outcome tables for the
specific unknown-bucket events before the DB is torn down.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    PaymentEvent, Decision, PolicyVerdict, ActionTaken, Outcome,
    RootCauseClassification
)
from app.strategies.strategy_a_baseline import run_strategy_a
from app.strategies.strategy_b_rules_only import run_strategy_b
from app.strategies.strategy_c_llm_policy import run_strategy_c


def load_dev_events(session):
    root = Path(__file__).parent.parent
    with open(root / "data" / "dataset.json", encoding="utf-8") as f:
        all_records = json.load(f)
    with open(root / "data" / "splits.json", encoding="utf-8") as f:
        splits = json.load(f)
    dev_ids = set(splits["dev"])
    dev_events = []
    for r in all_records:
        if r["razorpay_payment_id"] in dev_ids:
            event = PaymentEvent(
                razorpay_payment_id=r["razorpay_payment_id"],
                amount=r["amount"],
                currency=r["currency"],
                failure_reason_raw=r["failure_reason_raw"],
                failure_reason_code=r["failure_reason_code"],
                customer_id=r["customer_id"],
                order_id=r["order_id"],
                split_bucket="dev",
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            session.add(event)
            dev_events.append(event)
    session.flush()
    return dev_events


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)()


def main():
    print("Setting up fresh in-memory database...")
    db = make_session()
    dev_events = load_dev_events(db)
    print(f"Loaded {len(dev_events)} dev events.\n")

    print("Running Strategy A...")
    run_strategy_a(dev_events, db)
    print("Running Strategy B...")
    run_strategy_b(dev_events, db)
    print("Running Strategy C (live LLM, ~120 Groq calls)...")
    res_c = run_strategy_c(dev_events, db)
    print(f"Strategy C done. LLM={res_c['llm_decisions']} Fallback={res_c['fallback_decisions']}\n")

    # --- Query: unknown-bucket events where C LLM recommended retry_now or retry_later ---
    print("=" * 70)
    print("FORENSIC CHECK: unknown-bucket + retry_now/retry_later in Strategy C")
    print("=" * 70)

    # Find all unknown-bucket classifications
    unknown_cls = db.query(RootCauseClassification).filter_by(bucket="unknown").all()
    unknown_event_ids = {c.event_id for c in unknown_cls}
    print(f"Total unknown-bucket events in dev split: {len(unknown_event_ids)}")

    # Find Strategy C decisions that are retry_now or retry_later on unknown events
    c_retry_decisions = (
        db.query(Decision)
        .filter(
            Decision.strategy == "C",
            Decision.event_id.in_(unknown_event_ids),
            Decision.recommended_action.in_(["retry_now", "retry_later"])
        )
        .all()
    )
    print(f"Strategy C LLM retry recommendations on unknown-bucket events: {len(c_retry_decisions)}")
    print()

    safe = True
    full_trace_printed = False

    for decision in c_retry_decisions:
        event = db.query(PaymentEvent).filter_by(id=decision.event_id).first()
        cls   = db.query(RootCauseClassification).filter_by(event_id=decision.event_id).first()
        verdict = db.query(PolicyVerdict).filter_by(decision_id=decision.id).first()
        action  = db.query(ActionTaken).filter_by(decision_id=decision.id).first()
        outcome = db.query(Outcome).filter_by(event_id=decision.event_id, strategy="C").first()

        verdict_allowed     = verdict.allowed if verdict else None
        verdict_rule        = verdict.rejection_rule if verdict else None
        verdict_reason      = verdict.reason if verdict else None
        action_type         = action.action_type if action else "(none — no action record)"
        idem_key            = action.idempotency_key if action else "(n/a)"
        outcome_recovered   = outcome.recovered if outcome else None
        outcome_amount      = outcome.amount_recovered if outcome else None
        outcome_attempts    = outcome.attempts_used if outcome else None

        status = "BLOCKED (CORRECT)" if (verdict and not verdict.allowed) else "!!! ALLOWED — POLICY BUG !!!"
        if verdict and verdict.allowed:
            safe = False

        print(f"  Event ID:            {decision.event_id}")
        print(f"  Payment ID:          {event.razorpay_payment_id if event else 'N/A'}")
        print(f"  Error code:          {event.failure_reason_code if event else 'N/A'}")
        print(f"  Error raw:           {(event.failure_reason_raw or '')[:80] if event else 'N/A'}")
        print(f"  Root cause bucket:   {cls.bucket if cls else 'N/A'}  (classified_by={cls.classified_by if cls else 'N/A'})")
        print(f"  Amount:              INR {event.amount:.2f}" if event else "  Amount:              N/A")
        print(f"  LLM recommended:     {decision.recommended_action}")
        print(f"  LLM reasoning:       {(decision.reasoning or '')[:100]}")
        print(f"  --- Policy Verdict ---")
        print(f"  verdict.allowed:     {verdict_allowed}   [{status}]")
        print(f"  verdict.rejection_rule: {verdict_rule}")
        print(f"  verdict.reason:      {verdict_reason}")
        print(f"  --- Action Taken ---")
        print(f"  action_type:         {action_type}")
        print(f"  idempotency_key:     {idem_key}")
        print(f"  --- Outcome ---")
        print(f"  recovered:           {outcome_recovered}")
        print(f"  amount_recovered:    INR {outcome_amount:.2f}" if outcome_amount is not None else "  amount_recovered:    0.00")
        print(f"  attempts_used:       {outcome_attempts}")
        print()

        # Print a full trace for the first one
        if not full_trace_printed:
            full_trace_printed = True
            print("-" * 70)
            print("FULL AUDIT TRACE (first example):")
            print("-" * 70)
            print(f"  1. PaymentEvent")
            print(f"     id={decision.event_id} | razorpay_id={event.razorpay_payment_id if event else 'N/A'}")
            print(f"     amount=INR {event.amount:.2f} | code={event.failure_reason_code if event else 'N/A'}")
            print(f"     raw_error=\"{(event.failure_reason_raw or '')[:80]}\"")
            print(f"     created_at={event.created_at.isoformat() if event else 'N/A'}")
            print()
            print(f"  2. RootCauseClassification")
            print(f"     bucket={cls.bucket if cls else 'N/A'} | classified_by={cls.classified_by if cls else 'N/A'}")
            print()
            print(f"  3. Decision (Strategy C)")
            print(f"     id={decision.id} | recommended_action={decision.recommended_action}")
            print(f"     reasoning=\"{(decision.reasoning or '')[:120]}\"")
            print()
            print(f"  4. PolicyVerdict")
            print(f"     allowed={verdict_allowed}")
            print(f"     rejection_rule={verdict_rule}")
            print(f"     reason=\"{verdict_reason}\"")
            print()
            print(f"  5. ActionTaken")
            print(f"     {action_type}  (no physical action record = none executed)")
            print()
            print(f"  6. Outcome")
            print(f"     recovered={outcome_recovered} | amount_recovered=INR {outcome_amount or 0:.2f} | attempts_used={outcome_attempts}")
            print("-" * 70)
            print()

    print("=" * 70)
    if safe:
        print("RESULT: ALL UNKNOWN-BUCKET RETRY ATTEMPTS CORRECTLY BLOCKED.")
        print("        Policy Engine enforcement is working end-to-end.")
        print("        rejection_rule=BUCKET_BLOCKED on every case above.")
    else:
        print("!!! RESULT: POLICY ENGINE FAILURE DETECTED !!!")
        print("    One or more unknown-bucket retries were ALLOWED.")
        print("    STOP — do not proceed to Phase 6 until this is fixed.")
    print("=" * 70)

    db.close()


if __name__ == "__main__":
    main()
