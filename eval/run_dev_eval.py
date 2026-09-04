"""
Standalone dev-split evaluation script.
Runs Strategy A, B, and C on the dev split and produces:
  1. Side-by-side comparison table
  2. LLM vs fallback_heuristic source breakdown for Strategy C
  3. Disagreement analysis: where C LLM recommendation differed from B fixed rules

Does NOT touch held_out records.
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
from app.models import PaymentEvent, Decision, PolicyVerdict, RootCauseClassification
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


def print_comparison_table(res_a, res_b, res_c):
    print("\n" + "=" * 72)
    print("  DEV SPLIT -- STRATEGY A / B / C COMPARISON")
    print("=" * 72)
    print(f"{'Metric':<32} {'Strategy A':>12} {'Strategy B':>12} {'Strategy C':>12}")
    print("-" * 72)

    def row(label, a, b, c, fmt="{:.2f}"):
        print(f"{label:<32} {fmt.format(a):>12} {fmt.format(b):>12} {fmt.format(c):>12}")

    row("Total events",           res_a["total_events"],  res_b["total_events"],  res_c["total_events"],  fmt="{:.0f}")
    row("Recovered count",        res_a["recovered_count"], res_b["recovered_count"], res_c["recovered_count"], fmt="{:.0f}")
    row("Recovery rate (%)",      res_a["recovery_rate"], res_b["recovery_rate"],  res_c["recovery_rate"])
    row("Total INR recovered",    res_a["total_amount_recovered"], res_b["total_amount_recovered"], res_c["total_amount_recovered"])
    row("Total attempts",         res_a["total_attempts"], res_b["total_attempts"], res_c["total_attempts"], fmt="{:.0f}")
    row("INR per intervention",   res_a["recovery_per_intervention"], res_b["recovery_per_intervention"], res_c["recovery_per_intervention"])
    print("=" * 72)

    total_c = res_c["llm_decisions"] + res_c["fallback_decisions"]
    llm_pct = (res_c["llm_decisions"] / total_c * 100) if total_c else 0
    fb_pct  = (res_c["fallback_decisions"] / total_c * 100) if total_c else 0
    print(f"\n  Strategy C -- Decision Source Breakdown:")
    print(f"    LLM decisions:      {res_c['llm_decisions']:4d}  ({llm_pct:.1f}%)")
    print(f"    Fallback heuristic: {res_c['fallback_decisions']:4d}  ({fb_pct:.1f}%)")
    if res_c["fallback_decisions"] > 0:
        print(f"  WARNING: {res_c['fallback_decisions']} decisions fell back to heuristic -- check Groq errors above.")
    else:
        print(f"  OK: All {total_c} decisions came from the live LLM (zero fallbacks).")


def disagreement_analysis(db):
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
            # Detect source from reasoning prefix [LLM] or [FALLBACK]
            if reasoning.startswith("[LLM]"):
                source = "LLM"
                reasoning_body = reasoning[6:90]
            elif reasoning.startswith("[FALLBACK]"):
                source = "FALLBACK"
                reasoning_body = reasoning[11:90]
            else:
                source = "?"
                reasoning_body = reasoning[:80]
            disagree_rows.append({
                "event_id": eid,
                "bucket": bucket,
                "source": source,
                "b_action": db_d.recommended_action,
                "c_action": dc.recommended_action,
                "c_reasoning": reasoning_body,
            })

    total = agree + len(disagree_rows)
    print("\n" + "=" * 72)
    print("  DISAGREEMENT ANALYSIS -- Strategy C (LLM) vs Strategy B (Rules)")
    print("=" * 72)
    print(f"  Total events compared:  {total}")
    print(f"  Agreements:             {agree} ({agree/total*100:.1f}%)")
    print(f"  Disagreements:          {len(disagree_rows)} ({len(disagree_rows)/total*100:.1f}%)")
    print()

    if disagree_rows:
        print(f"  {'EvtID':>6}  {'Bucket':<22} {'Src':<8} {'B-action':<18} {'C-action':<18}  Reasoning (truncated)")
        print("  " + "-" * 106)
        for r in disagree_rows:
            print(f"  {r['event_id']:>6}  {r['bucket']:<22} {r['source']:<8} {r['b_action']:<18} {r['c_action']:<18}  {r['c_reasoning']}")

    from collections import Counter
    by_bucket    = Counter(r["bucket"] for r in disagree_rows)
    by_direction = Counter(f"{r['b_action']} -> {r['c_action']}" for r in disagree_rows)
    by_source    = Counter(r["source"] for r in disagree_rows)

    print()
    print("  Disagreements by source (LLM vs Fallback heuristic):")
    for src, cnt in by_source.most_common():
        print(f"    {src:<12}  {cnt:3d}")
    print()
    print("  Disagreements by root-cause bucket:")
    for bucket, cnt in by_bucket.most_common():
        print(f"    {bucket:<28}  {cnt:3d}")
    print()
    print("  Most common divergence patterns (B action -> C action):")
    for direction, cnt in by_direction.most_common(10):
        print(f"    {direction:<42}  {cnt:3d}")
    print("=" * 72)


def main():
    print("Initialising fresh in-memory database...")
    db = make_session()

    print("Loading dev split events...")
    dev_events = load_dev_events(db)
    print(f"  Loaded {len(dev_events)} dev events.")

    print("\nRunning Strategy A (Naive Baseline)...")
    res_a = run_strategy_a(dev_events, db)
    print(f"  Done. Recovered {res_a['recovered_count']}/{res_a['total_events']}.")

    print("\nRunning Strategy B (Rule-Only Policy)...")
    res_b = run_strategy_b(dev_events, db)
    print(f"  Done. Recovered {res_b['recovered_count']}/{res_b['total_events']}.")

    print("\nRunning Strategy C (LLM + Policy Engine)...")
    print("  Making Groq API calls for ~120 events (20s timeout each). This will take ~2-4 min...")
    res_c = run_strategy_c(dev_events, db)
    print(f"  Done. Recovered {res_c['recovered_count']}/{res_c['total_events']}.")
    print(f"  LLM: {res_c['llm_decisions']}  |  Fallback: {res_c['fallback_decisions']}")

    print_comparison_table(res_a, res_b, res_c)
    disagreement_analysis(db)

    db.close()
    print("\nEvaluation complete.")


if __name__ == "__main__":
    main()
