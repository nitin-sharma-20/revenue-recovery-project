"""
data/validate_dataset.py — Generation-time policy-window validation.

Runs immediately after dataset generation. Reports:
  1. Total record count vs. target (500)
  2. Per-bucket counts vs. targets — flags if any bucket is >10% off
  3. unknown-bucket count — exits code 1 if < 65 (DESIGN_DECISIONS.md item 16)
  4. Fraction of payments within the 7-day policy age window
  5. Fraction eligible for automated retry by instrument type (mandate vs. one_off_card)

Exits with code 1 on any constraint violation.
Run automatically at the top of eval/run_evaluation.py before any strategy executes.

Phase 1 stub — implement in Phase 1.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


UNKNOWN_MINIMUM = 65
TOTAL_TARGET = 500
BUCKET_TARGETS = {
    "soft_decline": 150,
    "insufficient_funds": 120,
    "network_error": 65,
    "hard_decline": 50,
    "risky": 50,
    "unknown": UNKNOWN_MINIMUM,
}
MAX_FAILURE_AGE_DAYS = 7
TOLERANCE_PCT = 0.10  # flag if any bucket is >10% off target


def validate(dataset_path: Path = Path("data/dataset.json")) -> bool:
    """
    Runs all validation checks and prints a report.

    Returns True if all checks pass, False otherwise.
    Exits with code 1 on failure when called as __main__.
    """
    if not dataset_path.exists():
        print(f"Error: {dataset_path} does not exist.")
        return False
        
    with open(dataset_path, "r", encoding="utf-8") as f:
        records = json.load(f)
        
    total_records = len(records)
    print(f"Total records: {total_records} (Target: {TOTAL_TARGET})")
    if total_records != TOTAL_TARGET:
        print(f"FAIL: Total records {total_records} != {TOTAL_TARGET}")
        return False

    buckets = {}
    instrument_types = {}
    within_policy_window = 0
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_FAILURE_AGE_DAYS)
    
    true_eligible_count = 0
    unknown_eligible_count = 0
    
    for r in records:
        b = r.get("bucket", "unknown")
        buckets[b] = buckets.get(b, 0) + 1
        
        it = r.get("instrument_type", "unknown")
        instrument_types[it] = instrument_types.get(it, 0) + 1
        
        created_at = datetime.fromisoformat(r["created_at"])
        is_within_window = created_at >= cutoff
        if is_within_window:
            within_policy_window += 1
            
        # Calculate TRUE eligibility intersection:
        # 1. Must be mandate (not one_off_card)
        # 2. Must be within the 7-day age window
        # 3. Must not be in a permanently blocked bucket (hard_decline, risky)
        # Note: unknown is technically eligible at the root-cause level, it just needs triage
        is_eligible = (
            it == "mandate" and
            is_within_window and
            b in ["soft_decline", "insufficient_funds", "network_error", "unknown"]
        )
        
        if is_eligible:
            true_eligible_count += 1
            if b == "unknown":
                unknown_eligible_count += 1

    print("\nBucket distribution:")
    all_passed = True
    for b, target in BUCKET_TARGETS.items():
        actual = buckets.get(b, 0)
        diff_pct = abs(actual - target) / target if target > 0 else 0
        status = "PASS"
        if b == "unknown":
            if actual < UNKNOWN_MINIMUM:
                status = f"FAIL (Below minimum {UNKNOWN_MINIMUM})"
                all_passed = False
        else:
            if diff_pct > TOLERANCE_PCT:
                status = f"FAIL (Diff > {TOLERANCE_PCT*100:.0f}%)"
                all_passed = False
        
        print(f"  {b:20s}: {actual:4d} (Target: {target:4d}) [{status}]")

    print(f"\nPolicy age window (<= {MAX_FAILURE_AGE_DAYS} days):")
    pct_window = (within_policy_window / total_records) * 100
    print(f"  {within_policy_window}/{total_records} ({pct_window:.1f}%)")
    
    print("\nInstrument types:")
    for it, count in instrument_types.items():
        pct = (count / total_records) * 100
        print(f"  {it:20s}: {count:4d} ({pct:.1f}%)")
        
    pct_eligible = (true_eligible_count / total_records) * 100
    print(f"\nFraction TRUE eligible for automated retry: {true_eligible_count}/{total_records} ({pct_eligible:.1f}%)")
    print(f"  (mandate AND within {MAX_FAILURE_AGE_DAYS} days AND bucket != hard_decline/risky)")
    
    unknown_total = buckets.get("unknown", 0)
    pct_unknown_eligible = (unknown_eligible_count / unknown_total) * 100 if unknown_total > 0 else 0
    print(f"\nUnknown bucket ablation potential:")
    print(f"  Total unknown records: {unknown_total}")
    print(f"  Eligible unknown records: {unknown_eligible_count}/{unknown_total} ({pct_unknown_eligible:.1f}%)")
    print(f"  (This is the maximum possible population where flag_on can diverge from flag_off)")

    if not all_passed:
        print("\nVALIDATION FAILED: Bucket targets violated.")
        return False
        
    print("\nVALIDATION PASSED")
    return True


if __name__ == "__main__":
    ok = validate()
    sys.exit(0 if ok else 1)
