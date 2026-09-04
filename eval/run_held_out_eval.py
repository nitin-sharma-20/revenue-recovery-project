"""
Final Phase 6 Evaluation Script.
Runs Strategy A, B, and C on the held_out split exactly once.
Generates eval/report.md containing metrics and the exception list.

DATA INTEGRITY GUARANTEE:
  - Uses sqlite:///:memory: — a FRESH in-memory DB every run, so there can be
    NO stale Decision/Outcome rows from any prior execution.
  - After all strategies run, sanity_check_no_duplicate_decisions() asserts
    that every (event_id, strategy) pair has exactly ONE Decision row.
    If duplicates are found (e.g. due to a future switch to a file DB) the
    script aborts with exit code 2 before writing the report.
  - The module-level _DATASET_CACHE in generate_synthetic_data.py is explicitly
    cleared at startup to prevent any cross-run cache bleed if this module is
    imported more than once in the same process.
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
from app.models import PaymentEvent, Decision, PolicyVerdict, RootCauseClassification, Outcome
from app.strategies.strategy_a_baseline import run_strategy_a
from app.strategies.strategy_b_rules_only import run_strategy_b
from app.strategies.strategy_c_llm_policy import run_strategy_c


def load_held_out_events(session):
    root = Path(__file__).parent.parent
    with open(root / "data" / "dataset.json", encoding="utf-8") as f:
        all_records = json.load(f)
    with open(root / "data" / "splits.json", encoding="utf-8") as f:
        splits = json.load(f)

    held_out_ids = set(splits["held_out"])
    events = []
    for r in all_records:
        if r["razorpay_payment_id"] in held_out_ids:
            event = PaymentEvent(
                razorpay_payment_id=r["razorpay_payment_id"],
                amount=r["amount"],
                currency=r["currency"],
                failure_reason_raw=r["failure_reason_raw"],
                failure_reason_code=r["failure_reason_code"],
                customer_id=r["customer_id"],
                order_id=r["order_id"],
                split_bucket="held_out",
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            session.add(event)
            events.append(event)
    session.flush()
    return events


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)()


def get_exceptions(db, strategy: str):
    """Returns a list of payment events that were NOT recovered by the strategy."""
    outcomes = db.query(Outcome).filter_by(strategy=strategy, recovered=False).all()
    event_ids = [o.event_id for o in outcomes]
    events = db.query(PaymentEvent).filter(PaymentEvent.id.in_(event_ids)).all()
    classifications = {c.event_id: c.bucket for c in db.query(RootCauseClassification).filter(RootCauseClassification.event_id.in_(event_ids)).all()}
    
    exceptions = []
    for e in events:
        bucket = classifications.get(e.id, "unknown")
        reason_cat = "Policy-Blocked (Never Attempted)" if bucket in ["hard_decline", "risky", "unknown"] else "Ground-Truth Unrecoverable (Attempted, Failed)"
        exceptions.append({
            "payment_id": e.razorpay_payment_id,
            "amount": e.amount,
            "bucket": bucket,
            "error_code": e.failure_reason_code,
            "error_raw": e.failure_reason_raw,
            "reason_category": reason_cat
        })
    return exceptions

def generate_disagreement_markdown(db) -> str:
    decisions_b = {d.event_id: d for d in db.query(Decision).filter_by(strategy="B").all()}
    decisions_c = {d.event_id: d for d in db.query(Decision).filter_by(strategy="C").all()}
    classifications = {c.event_id: c for c in db.query(RootCauseClassification).all()}

    agree = 0
    disagree_rows = []

    for eid, dc in decisions_c.items():
        db_d = decisions_b.get(eid)
        if not db_d:
            continue
        if dc.recommended_action == db_d.recommended_action:
            agree += 1
        else:
            cls = classifications.get(eid)
            bucket = cls.bucket if cls else "unknown"
            reasoning = dc.reasoning or ""
            source = "llm" if "[LLM]" in reasoning or "llm" in reasoning.lower() else "fallback"
            if "]:" in reasoning:
                reasoning_body = reasoning.split("]:", 1)[1].strip()
            else:
                reasoning_body = reasoning
            disagree_rows.append({
                "event_id": eid,
                "bucket": bucket,
                "source": source,
                "b_action": db_d.recommended_action,
                "c_action": dc.recommended_action,
                "c_reasoning": reasoning_body,
            })

    total = agree + len(disagree_rows)
    md = f"## 3. Disagreement Analysis: Strategy C (LLM) vs Strategy B (Rules)\n\n"
    md += f"- **Total events compared:** {total}\n"
    md += f"- **Agreements:** {agree} ({agree/total*100:.1f}%)\n"
    md += f"- **Disagreements:** {len(disagree_rows)} ({len(disagree_rows)/total*100:.1f}%)\n\n"

    if disagree_rows:
        md += "| Event ID | Bucket | B Action | C Action | C Reasoning |\n"
        md += "|---|---|---|---|---|\n"
        for r in disagree_rows:
            md += f"| {r['event_id']} | `{r['bucket']}` | `{r['b_action']}` | `{r['c_action']}` | {r['c_reasoning'][:80]}... |\n"
    
    return md

def generate_report_md(res_a, res_b, res_c, exceptions_a, exceptions_b, exceptions_c, db):
    root = Path(__file__).parent.parent
    report_path = root / "eval" / "report.md"
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# Reclaim Evaluation Report (Held-Out Split)\n\n")
        f.write("This report compares the performance of Strategy A (Naive), Strategy B (Rules-Only), and Strategy C (LLM + Policy Engine) on the final, untouched `held_out` split.\n\n")
        
        f.write("## 1. Recovery Metrics\n\n")
        f.write("| Metric | Strategy A | Strategy B | Strategy C |\n")
        f.write("|---|---|---|---|\n")
        f.write(f"| Total events | {res_a['total_events']} | {res_b['total_events']} | {res_c['total_events']} |\n")
        f.write(f"| Recovered count | {res_a['recovered_count']} | {res_b['recovered_count']} | {res_c['recovered_count']} |\n")
        f.write(f"| Recovery rate (%) | {res_a['recovery_rate']:.2f}% | {res_b['recovery_rate']:.2f}% | {res_c['recovery_rate']:.2f}% |\n")
        f.write(f"| Total INR recovered | Rs.{res_a['total_amount_recovered']:,.2f} | Rs.{res_b['total_amount_recovered']:,.2f} | Rs.{res_c['total_amount_recovered']:,.2f} |\n")
        f.write(f"| Total attempts | {res_a['total_attempts']} | {res_b['total_attempts']} | {res_c['total_attempts']} |\n")
        f.write(f"| INR per intervention | Rs.{res_a['recovery_per_intervention']:,.2f} | Rs.{res_b['recovery_per_intervention']:,.2f} | Rs.{res_c['recovery_per_intervention']:,.2f} |\n\n")
        
        total_c = res_c["llm_decisions"] + res_c["fallback_decisions"]
        if total_c > 0:
            llm_pct = (res_c["llm_decisions"] / total_c) * 100
        else:
            llm_pct = 0
            
        f.write("### Strategy C Source Breakdown\n")
        f.write(f"- **LLM Decisions:** {res_c['llm_decisions']} ({llm_pct:.1f}%)\n")
        f.write(f"- **Fallback Heuristic:** {res_c['fallback_decisions']}\n\n")
        
        f.write("## 2. Exception List (Unrecovered Payments)\n\n")
        f.write("The following payments were fundamentally unrecoverable or exhausted their retry attempts.\n\n")
        
        def write_exception_table(exceptions, strategy_name):
            f.write(f"### {strategy_name} Exceptions ({len(exceptions)})\n\n")
            if not exceptions:
                f.write("No exceptions.\n\n")
                return
                
            f.write("| Payment ID | Amount | Root Cause Bucket | Reason Category | Error Code | Raw Error |\n")
            f.write("|---|---|---|---|---|---|\n")
            for ex in exceptions:
                f.write(f"| `{ex['payment_id']}` | Rs.{ex['amount']:.2f} | `{ex['bucket']}` | {ex['reason_category']} | `{ex['error_code']}` | {ex['error_raw'][:40]}... |\n")
            f.write("\n")
            
        write_exception_table(exceptions_a, "Strategy A")
        write_exception_table(exceptions_b, "Strategy B")
        write_exception_table(exceptions_c, "Strategy C")

        f.write(generate_disagreement_markdown(db))

    print(f"Report successfully written to {report_path}")


def sanity_check_no_duplicate_decisions(db, events, strategies=("A", "B", "C")):
    """
    SANITY ASSERTION: verify that each (event_id, strategy) pair has exactly ONE
    Decision row after the run. If duplicates exist, the run is corrupt and we
    abort before writing the report.

    This guard exists to catch any future scenario where the eval is switched
    from an in-memory DB to a file-backed DB and rows from a previous run are
    not cleared before re-execution.
    """
    print("--- Sanity Assertion: Decision row counts per (event_id, strategy) ---")
    violations = []
    for strategy in strategies:
        for event in events:
            count = db.query(Decision).filter_by(event_id=event.id, strategy=strategy).count()
            if count != 1:
                violations.append((event.razorpay_payment_id, strategy, count))
                print(f"  [FAIL] pid={event.razorpay_payment_id} | strategy={strategy} | rows={count}")

    total_checked = len(events) * len(strategies)
    if violations:
        print(f"\n[ABORT] {len(violations)}/{total_checked} pairs have duplicate/missing Decision rows.")
        print("        This indicates stale data from a prior run — fix the harness before")
        print("        re-running so the report is not computed over mixed old+new decisions.")
        sys.exit(2)
    else:
        print(f"  [PASS] All {total_checked} (event_id, strategy) pairs have exactly 1 Decision row.")
        print("         No stale or duplicate data detected. Report is clean.\n")


def main():
    # Force UTF-8 output so currency symbols render on Windows terminals
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 70)
    print("Reclaim -- Phase 6 Held-Out Evaluation")
    print("DB: sqlite:///:memory: (fresh in-memory instance per run)")
    print("    => Zero stale rows possible by construction.")
    print("=" * 70)
    print()

    print("Initialising fresh in-memory database...")
    db = make_session()

    # Clear module-level dataset cache in case this script is imported or re-run
    # in the same process (e.g. during test harness execution). This prevents any
    # cross-run contamination via the _DATASET_CACHE global in generate_synthetic_data.py.
    import data.generate_synthetic_data as gsd
    gsd._DATASET_CACHE.clear()
    print("  Module-level _DATASET_CACHE cleared (prevents in-process cache bleed).")

    print("Loading held_out split events...")
    events = load_held_out_events(db)
    print(f"  Loaded {len(events)} held_out events.")
    
    if len(events) == 0:
        print("Error: No events found in held_out split.")
        sys.exit(1)

    print("\nRunning Strategy A (Naive Baseline)...")
    res_a = run_strategy_a(events, db)
    
    print("\nRunning Strategy B (Rule-Only Policy)...")
    res_b = run_strategy_b(events, db)

    print("\nRunning Strategy C (LLM + Policy Engine)...")
    res_c = run_strategy_c(events, db)
    
    # Per-event provider/source log for Strategy C
    print("\n--- Strategy C Per-Event Provider/Source Log ---")
    decisions_c = db.query(Decision).filter_by(strategy="C").all()
    llm_count_log = 0
    fallback_count_log = 0
    for d in decisions_c:
        src = d.reasoning.split("]:")[0].strip("[")
        is_llm = src == "LLM"
        tag = "LLM     " if is_llm else "FALLBACK"
        print(f"  [{tag}] event_id={d.event_id} | action={d.recommended_action}")
        if is_llm:
            llm_count_log += 1
        else:
            fallback_count_log += 1
    print(f"  Summary: {llm_count_log} LLM decisions, {fallback_count_log} fallback decisions")
    print("------------------------------------------------\n")

    # === SANITY ASSERTION: exactly 1 Decision row per (event, strategy) ===
    # This assertion will abort with exit code 2 if duplicates exist,
    # preventing a corrupt report from being written.
    sanity_check_no_duplicate_decisions(db, events, strategies=("A", "B", "C"))

    exc_a = get_exceptions(db, "A")
    exc_b = get_exceptions(db, "B")
    exc_c = get_exceptions(db, "C")

    generate_report_md(res_a, res_b, res_c, exc_a, exc_b, exc_c, db)

    # Persist Strategy C decision reasoning to disk so it survives the in-memory DB teardown.
    # Keyed by razorpay_payment_id for easy lookup.
    decisions_dump = []
    for d in db.query(Decision).filter_by(strategy="C").all():
        event = db.query(PaymentEvent).filter_by(id=d.event_id).first()
        src_tag = d.reasoning.split("]:")[0].strip("[")
        reasoning_body = d.reasoning.split("]:", 1)[1].strip() if "]:" in d.reasoning else d.reasoning
        decisions_dump.append({
            "event_id": d.event_id,
            "razorpay_payment_id": event.razorpay_payment_id if event else None,
            "bucket": next(
                (o["bucket"] for o in res_c["outcomes"] if o["event_id"] == d.event_id), "unknown"
            ),
            "source": src_tag,
            "action": d.recommended_action,
            "full_reasoning": reasoning_body,
        })

    dump_path = Path(__file__).parent / "strategy_c_decisions.json"
    with open(dump_path, "w", encoding="utf-8") as f:
        json.dump(decisions_dump, f, indent=2, ensure_ascii=False)
    print(f"Strategy C decision reasoning written to {dump_path}")

    db.close()
    print("\nEvaluation complete.")


if __name__ == "__main__":
    main()
