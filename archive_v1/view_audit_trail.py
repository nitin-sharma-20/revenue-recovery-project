"""
eval/view_audit_trail.py — Reclaim Audit Trail CLI Viewer

Prints the full end-to-end lifecycle trace for every strategy run on a given
payment_id, reading from a persistent SQLite file.

Usage:
    python eval/view_audit_trail.py <razorpay_payment_id>
    python eval/view_audit_trail.py pay_synth_0097_6e39c0
    python eval/view_audit_trail.py pay_synth_0097_6e39c0 --strategy C

    # Held-out split (after running run_held_out_eval.py first):
    python eval/view_audit_trail.py pay_synth_0034_64d756 --db eval/held_out.db
    python eval/view_audit_trail.py pay_synth_0034_64d756 --db eval/held_out.db --strategy C
"""

import sys
import argparse
from pathlib import Path
from datetime import timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    PaymentEvent, RootCauseClassification, Decision,
    PolicyVerdict, ActionTaken, Outcome
)

DEFAULT_DB_PATH = Path(__file__).parent.parent / "reclaim.db"
HELD_OUT_DB_PATH = Path(__file__).parent / "held_out.db"

SEP   = "=" * 70
HSEP  = "-" * 70
DSEP  = "·" * 70


def fmt_ts(dt):
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def print_block(title: str, lines: list[str]):
    print(f"\n  ┌─ {title}")
    for line in lines:
        print(f"  │  {line}")
    print(f"  └{'─' * (len(title) + 2)}")


def render_event(event: PaymentEvent):
    print(SEP)
    print(f"  PAYMENT EVENT")
    print(HSEP)
    print(f"  ID              : {event.id}")
    print(f"  Razorpay ID     : {event.razorpay_payment_id}")
    print(f"  Amount          : Rs.{event.amount:,.2f} {event.currency}")
    print(f"  Customer        : {event.customer_id}")
    print(f"  Order           : {event.order_id}")
    print(f"  Failed At       : {fmt_ts(event.created_at)}")
    print(f"  Split Bucket    : {event.split_bucket or '—'}")
    print(f"  Error Code      : {event.failure_reason_code or '—'}")
    print(f"  Error Raw       : {event.failure_reason_raw or '—'}")


def render_classification(cls: RootCauseClassification | None):
    print()
    print(DSEP)
    print("  ROOT CAUSE CLASSIFICATION")
    print(HSEP)
    if cls is None:
        print("  (no classification found)")
        return
    print(f"  Bucket          : {cls.bucket}")
    print(f"  Classified By   : {cls.classified_by}")
    print(f"  Classified At   : {fmt_ts(cls.created_at)}")


def render_strategy_trace(strategy: str, decision: Decision | None, db):
    print()
    print(DSEP)
    print(f"  STRATEGY {strategy} TRACE")
    print(HSEP)

    if decision is None:
        print(f"  (no Decision row found for strategy {strategy})")
        return

    # Parse source tag
    reasoning_raw = decision.reasoning or ""
    if "]:" in reasoning_raw:
        src_tag = reasoning_raw.split("]:")[0].strip("[")
        reasoning_body = reasoning_raw.split("]:", 1)[1].strip()
    else:
        src_tag = "UNKNOWN"
        reasoning_body = reasoning_raw

    print(f"  Decision ID     : {decision.id}")
    print(f"  Recommended     : {decision.recommended_action}")
    print(f"  Source          : {src_tag}")
    print(f"  Decided At      : {fmt_ts(decision.created_at)}")
    print_block("Reasoning", [
        line for line in (
            reasoning_body[i:i+64] for i in range(0, len(reasoning_body), 64)
        )
    ])

    # Policy Verdict
    verdict = db.query(PolicyVerdict).filter_by(decision_id=decision.id).first()
    print()
    print("  POLICY VERDICT")
    print(HSEP)
    if verdict is None:
        print("  (no verdict found — policy engine was not called)")
    else:
        status = "✅ APPROVED" if verdict.allowed else "🚫 BLOCKED"
        print(f"  Result          : {status}")
        print(f"  Verdict At      : {fmt_ts(verdict.created_at)}")
        if not verdict.allowed:
            print(f"  Rejection Rule  : {verdict.rejection_rule or '—'}")
        print_block("Policy Reason", [
            line for line in (
                verdict.reason[i:i+64] for i in range(0, len(verdict.reason), 64)
            )
        ])

    # Actions Taken
    actions = db.query(ActionTaken).filter_by(decision_id=decision.id).all()
    print()
    print("  ACTION TAKEN")
    print(HSEP)
    if not actions:
        print("  (no action executed — verdict was blocked or action was non-executable)")
    else:
        for a in actions:
            print(f"  Action Type     : {a.action_type}")
            print(f"  Idempotency Key : {a.idempotency_key}")
            print(f"  Executed At     : {fmt_ts(a.executed_at)}")
            if a.razorpay_response:
                print(f"  API Response    : {a.razorpay_response[:80]}")

    # Outcome
    outcome = db.query(Outcome).filter_by(event_id=decision.event_id, strategy=strategy).first()
    print()
    print("  OUTCOME")
    print(HSEP)
    if outcome is None:
        print("  (no outcome row found)")
    else:
        recovered_str = "✅ RECOVERED" if outcome.recovered else "❌ NOT RECOVERED"
        print(f"  Result          : {recovered_str}")
        print(f"  Amount          : Rs.{outcome.amount_recovered:,.2f}")
        print(f"  Attempts Used   : {outcome.attempts_used}")
        print(f"  Recorded At     : {fmt_ts(outcome.created_at)}")


def fetch_trace_data(payment_id: str, db_path: Path) -> dict:
    """
    Fetches full structured lifecycle trace data for a given payment_id.
    Reused directly by CLI and Streamlit presentation UI.
    """
    if not db_path.exists():
        return {"error": f"Database not found at {db_path}"}

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False}
    )
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        event = db.query(PaymentEvent).filter_by(razorpay_payment_id=payment_id).first()
        if event is None:
            return {"error": f"No PaymentEvent found with razorpay_payment_id='{payment_id}' in {db_path.name}"}

        cls = db.query(RootCauseClassification).filter_by(event_id=event.id).first()

        strategies = {}
        for strategy in ["A", "B", "C"]:
            decision = db.query(Decision).filter_by(
                event_id=event.id, strategy=strategy
            ).order_by(Decision.id.desc()).first()

            if decision is None:
                strategies[strategy] = None
                continue

            reasoning_raw = decision.reasoning or ""
            if "]:" in reasoning_raw:
                src_tag = reasoning_raw.split("]:")[0].strip("[")
                reasoning_body = reasoning_raw.split("]:", 1)[1].strip()
            else:
                src_tag = "UNKNOWN"
                reasoning_body = reasoning_raw

            verdict = db.query(PolicyVerdict).filter_by(decision_id=decision.id).first()
            actions = db.query(ActionTaken).filter_by(decision_id=decision.id).all()
            outcome = db.query(Outcome).filter_by(event_id=decision.event_id, strategy=strategy).first()

            strategies[strategy] = {
                "decision_id": decision.id,
                "recommended_action": decision.recommended_action,
                "source": src_tag,
                "reasoning": reasoning_body,
                "decided_at": decision.created_at,
                "verdict": {
                    "allowed": verdict.allowed,
                    "reason": verdict.reason,
                    "rejection_rule": verdict.rejection_rule,
                    "created_at": verdict.created_at
                } if verdict else None,
                "actions": [
                    {
                        "action_type": a.action_type,
                        "idempotency_key": a.idempotency_key,
                        "executed_at": a.executed_at,
                        "razorpay_response": a.razorpay_response
                    } for a in actions
                ],
                "outcome": {
                    "recovered": outcome.recovered,
                    "amount_recovered": outcome.amount_recovered,
                    "attempts_used": outcome.attempts_used,
                    "created_at": outcome.created_at
                } if outcome else None
            }

        return {
            "error": None,
            "event": {
                "id": event.id,
                "razorpay_payment_id": event.razorpay_payment_id,
                "amount": event.amount,
                "currency": event.currency,
                "customer_id": event.customer_id,
                "order_id": event.order_id,
                "created_at": event.created_at,
                "split_bucket": event.split_bucket,
                "failure_reason_code": event.failure_reason_code,
                "failure_reason_raw": event.failure_reason_raw
            },
            "classification": {
                "bucket": cls.bucket,
                "classified_by": cls.classified_by,
                "created_at": cls.created_at
            } if cls else None,
            "strategies": strategies
        }
    finally:
        db.close()


def get_all_payment_ids(db_path: Path) -> list[str]:
    """Returns list of razorpay_payment_id strings available in db_path."""
    if not db_path.exists():
        return []
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False}
    )
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        events = db.query(PaymentEvent.razorpay_payment_id).order_by(PaymentEvent.id).all()
        return [e[0] for e in events]
    finally:
        db.close()


def run(payment_id: str, strategy_filter: str | None, db_path: Path):
    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}")
        if db_path == DEFAULT_DB_PATH:
            print("       Run the FastAPI server at least once (it calls init_db()) to create it,")
            print("       or use: python eval/run_dev_eval.py  (populates reclaim.db with dev data)")
        elif db_path == HELD_OUT_DB_PATH or str(db_path).endswith("held_out.db"):
            print("       Run the held-out evaluation first:")
            print("         python eval/run_held_out_eval.py")
            print("       This creates eval/held_out.db with the full held-out audit trail.")
        else:
            print(f"       The path '{db_path}' does not exist.")
        sys.exit(1)

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False}
    )
    Session = sessionmaker(bind=engine)
    db = Session()

    event = db.query(PaymentEvent).filter_by(razorpay_payment_id=payment_id).first()
    if event is None:
        split_hint = ""
        if str(db_path).endswith("reclaim.db"):
            split_hint = "\n       The dev eval populates reclaim.db: python eval/run_dev_eval.py"
        elif str(db_path).endswith("held_out.db"):
            split_hint = "\n       Ensure you ran: python eval/run_held_out_eval.py"
        print(f"ERROR: No PaymentEvent found with razorpay_payment_id='{payment_id}'{split_hint}")
        db.close()
        sys.exit(1)

    render_event(event)

    # Root cause (shared across strategies)
    cls = db.query(RootCauseClassification).filter_by(event_id=event.id).first()
    render_classification(cls)

    # Determine which strategies to show
    strategies = ["A", "B", "C"] if strategy_filter is None else [strategy_filter.upper()]

    for strategy in strategies:
        decision = db.query(Decision).filter_by(
            event_id=event.id, strategy=strategy
        ).order_by(Decision.id.desc()).first()
        render_strategy_trace(strategy, decision, db)

    print()
    print(SEP)
    print(f"  End of audit trail for {payment_id}")
    print(SEP)
    db.close()



def main():
    parser = argparse.ArgumentParser(
        description="Reclaim Audit Trail Viewer — print full lifecycle trace for a payment"
    )
    parser.add_argument(
        "payment_id",
        help="Razorpay payment ID to trace (e.g. pay_synth_0097_6e39c0)"
    )
    parser.add_argument(
        "--strategy",
        choices=["A", "B", "C", "a", "b", "c"],
        default=None,
        help="Filter to a single strategy (A/B/C). Default: show all three."
    )
    parser.add_argument(
        "--db",
        default=None,
        metavar="PATH",
        help=(
            "Path to the SQLite database file. "
            "Defaults to reclaim.db (dev split). "
            "Use eval/held_out.db for held-out split traces "
            "(requires running run_held_out_eval.py first)."
        )
    )
    args = parser.parse_args()

    # Resolve db path: explicit --db wins, else default reclaim.db
    db_path = Path(args.db) if args.db else DEFAULT_DB_PATH
    # Resolve relative paths from the project root (where the user likely runs from)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path

    # Force UTF-8 on Windows terminals
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    run(args.payment_id, args.strategy, db_path)


if __name__ == "__main__":
    main()
