"""
data/ground_truth.py — Synthetic ground-truth outcome model.

DISCLOSURE: Recovery outcomes in this system are determined by the synthetic
ground-truth model defined in this module, NOT by live bank/issuer authorization
behavior. Razorpay test-mode API calls are made to verify executor plumbing, but the
test-mode Razorpay API does not perform real authorization — it simulates
success/failure responses. Reported INR recovered figures reflect synthetic model
outcomes only and should not be interpreted as real-world authorization results.

ARCHITECTURE RULE (DESIGN_DECISIONS.md item 8):
  This module has ZERO imports from app/strategies/, app/llm_recommender.py,
  app/policy_engine.py, or app/executor.py. The import direction is one-way:
  app/ imports from this module (read-only lookup). Never the reverse.
  Enforced by a CI test in tests/test_import_boundary.py.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ALLOWED IMPORTS: only stdlib. No app/ imports ever.

DATASET_VERSION = "v2"

# ── Root cause buckets ────────────────────────────────────────────────────────
HARD_DECLINE = "hard_decline"
SOFT_DECLINE = "soft_decline"
INSUFFICIENT_FUNDS = "insufficient_funds"
NETWORK_ERROR = "network_error"
RISKY = "risky"
UNKNOWN = "unknown"

# ── Instrument types (DESIGN_DECISIONS.md item 3) ────────────────────────────
INSTRUMENT_MANDATE = "mandate"        # UPI Autopay / NACH recurring — retries valid
INSTRUMENT_ONE_OFF_CARD = "one_off_card"  # One-off card charge — retries invalid

# ── Stratified sampling targets (DESIGN_DECISIONS.md item 11) ────────────────
# Total pool: 500 records. unknown hard minimum: 65.
BUCKET_TARGETS = {
    SOFT_DECLINE: 150,
    INSUFFICIENT_FUNDS: 120,
    NETWORK_ERROR: 65,
    HARD_DECLINE: 50,
    RISKY: 50,
    UNKNOWN: 65,  # HARD MINIMUM — enforced before proportional fill
}
TOTAL_RECORDS = 500
UNKNOWN_MINIMUM = 65


# ── Module-level dataset cache ────────────────────────────────────────────────
# Reset between processes; explicitly cleared at eval startup to prevent
# in-process cache bleed (DESIGN_DECISIONS.md item 12).
_DATASET_CACHE: Dict[str, Dict[str, Any]] = {}


def clear_cache() -> None:
    """Clear the module-level dataset cache. Call at the top of any eval run."""
    _DATASET_CACHE.clear()


# ── Failure scenarios by bucket ───────────────────────────────────────────────
FAILURE_SCENARIOS: Dict[str, List[tuple[str, str]]] = {
    HARD_DECLINE: [
        ("stolen_card", "Issuer reported card as stolen or lost."),
        ("card_expired", "The card has expired."),
        ("account_closed", "Customer account closed."),
    ],
    SOFT_DECLINE: [
        ("do_not_honor", "Issuer declined the request temporarily (do not honor)."),
        ("limit_exceeded", "Customer's daily transaction limit exceeded."),
    ],
    INSUFFICIENT_FUNDS: [
        ("insufficient_funds", "Customer has insufficient funds for this transaction."),
    ],
    NETWORK_ERROR: [
        ("gateway_timeout", "Razorpay gateway timed out communicating with issuer."),
        ("network_error", "Transient network error during payment processing."),
    ],
    RISKY: [
        ("suspected_fraud", "Razorpay risk engine flagged transaction as suspicious."),
    ],
    UNKNOWN: [
        ("unknown_error", "An unknown error occurred during processing."),
        ("processing_failed", "General processing failure. Please contact support."),
        (None, "Free-text bank response: please try again later."),
        (None, "Error 99: unmapped gateway response."),
    ],
}

INSTRUMENT_WEIGHTS = {
    INSTRUMENT_MANDATE: 0.45,      # 45% recurring/mandate (retries valid)
    INSTRUMENT_ONE_OFF_CARD: 0.55, # 55% one-off card (retries invalid)
}


def compute_ground_truth_outcomes(
    bucket: str,
    instrument_type: str,
    is_unrecoverable: bool,
    amount: float,
    rng: random.Random,
) -> Dict[str, Any]:
    """
    Computes fixed, independent ground-truth outcomes for every possible action.

    ARCHITECTURE RULE: This function is called at generation time, before any
    strategy or recommender exists. It must never be called from within a strategy
    or recommender — only from generate_dataset() and get_ground_truth_outcome().

    Returns a dict keyed by action name, each value is:
        {"recovered": bool, "amount_recovered": float, "attempts_used": int}
    """
    outcomes = {}

    # Define base recovery probabilities based on bucket and unrecoverable flag
    if is_unrecoverable or bucket in (HARD_DECLINE, RISKY):
        base_prob = 0.0
    elif bucket == NETWORK_ERROR:
        base_prob = 0.9  # Highly recoverable
    elif bucket == INSUFFICIENT_FUNDS:
        base_prob = 0.4  # Moderately recoverable if tried later
    elif bucket == SOFT_DECLINE:
        base_prob = 0.6  # Moderately recoverable
    else: # UNKNOWN
        base_prob = 0.5  # Mixed

    # retry_now
    # If instrument is one_off_card, it's virtually impossible to recover via auto retry
    if instrument_type == INSTRUMENT_ONE_OFF_CARD:
        prob_retry_now = 0.0
    elif bucket == INSUFFICIENT_FUNDS:
        prob_retry_now = 0.1 # rarely works immediately
    else:
        prob_retry_now = base_prob * 0.8
    outcomes["retry_now"] = {
        "recovered": rng.random() < prob_retry_now,
        "amount_recovered": amount if rng.random() < prob_retry_now else 0.0,
        "attempts_used": 1
    }

    # retry_later
    if instrument_type == INSTRUMENT_ONE_OFF_CARD:
        prob_retry_later = 0.0
    elif bucket == INSUFFICIENT_FUNDS:
        prob_retry_later = base_prob * 0.9 # Better chance later
    elif bucket == NETWORK_ERROR:
        prob_retry_later = base_prob * 0.5 # Better to try now, later it might have expired
    else:
        prob_retry_later = base_prob
    outcomes["retry_later"] = {
        "recovered": rng.random() < prob_retry_later,
        "amount_recovered": amount if rng.random() < prob_retry_later else 0.0,
        "attempts_used": 1
    }
    
    # switch_method
    prob_switch = base_prob * 0.7 if not is_unrecoverable else 0.0
    if bucket in (HARD_DECLINE, INSUFFICIENT_FUNDS):
        prob_switch = 0.6 if not is_unrecoverable else 0.0 # Switching method is good for these
    outcomes["switch_method"] = {
        "recovered": rng.random() < prob_switch,
        "amount_recovered": amount if rng.random() < prob_switch else 0.0,
        "attempts_used": 0 # Prompts customer, no automated attempt used
    }

    # escalate_human
    prob_human = base_prob * 0.5 if not is_unrecoverable else 0.0
    outcomes["escalate_human"] = {
        "recovered": rng.random() < prob_human,
        "amount_recovered": amount if rng.random() < prob_human else 0.0,
        "attempts_used": 0
    }

    # stop
    outcomes["stop"] = {
        "recovered": False,
        "amount_recovered": 0.0,
        "attempts_used": 0
    }

    return outcomes


def generate_record(
    index: int,
    bucket: str,
    base_time: datetime,
    rng: random.Random,
) -> Dict[str, Any]:
    """
    Generates a single synthetic payment.failed record with instrument_type tag
    and pre-computed independent ground-truth outcomes.
    """
    error_code, error_desc = rng.choice(FAILURE_SCENARIOS[bucket])
    
    amount = round(rng.uniform(100.0, 10000.0), 2)
    instrument_type = rng.choices(
        list(INSTRUMENT_WEIGHTS.keys()),
        weights=list(INSTRUMENT_WEIGHTS.values())
    )[0]
    
    # Age of failure between 0 and 10 days ago.
    # 7 days is the policy cutoff, so we want some beyond that.
    days_ago = rng.uniform(0, 10)
    created_at = base_time - timedelta(days=days_ago)

    is_unrecoverable = rng.random() < 0.20 # 20% are genuinely unrecoverable

    outcomes = compute_ground_truth_outcomes(
        bucket=bucket,
        instrument_type=instrument_type,
        is_unrecoverable=is_unrecoverable,
        amount=amount,
        rng=rng
    )

    return {
        "id": index,
        "razorpay_payment_id": f"pay_{rng.randint(100000, 999999)}",
        "amount": amount,
        "currency": "INR",
        "failure_reason_raw": error_desc,
        "failure_reason_code": error_code,
        "customer_id": f"cust_{rng.randint(1000, 9999)}",
        "order_id": f"order_{rng.randint(10000, 99999)}",
        "created_at": created_at.isoformat(),
        "bucket": bucket, # Keep this around for easy validation
        "instrument_type": instrument_type,
        "dataset_version": DATASET_VERSION,
        "ground_truth_outcomes": outcomes, # Pre-computed
    }


def generate_dataset(
    num_records: int = TOTAL_RECORDS,
    seed: int = 42,
) -> List[Dict[str, Any]]:
    """
    Generates deterministic synthetic dataset using stratified sampling.

    DESIGN_DECISIONS.md item 11: unknown bucket filled to UNKNOWN_MINIMUM
    (65) first, then remaining records distributed proportionally.

    Returns: list of record dicts (NOT split — pool is kept whole for bootstrap).
    """
    rng = random.Random(seed)
    base_time = datetime.now(timezone.utc)
    
    buckets_to_generate = []
    
    # Hard minimum for unknown
    buckets_to_generate.extend([UNKNOWN] * UNKNOWN_MINIMUM)
    
    # Fill the rest proportionally
    remaining_records = num_records - UNKNOWN_MINIMUM
    if remaining_records > 0:
        total_other_targets = sum(tgt for b, tgt in BUCKET_TARGETS.items() if b != UNKNOWN)
        for b, tgt in BUCKET_TARGETS.items():
            if b == UNKNOWN:
                continue
            count = int(round((tgt / total_other_targets) * remaining_records))
            buckets_to_generate.extend([b] * count)
    
    # Adjust for rounding errors
    while len(buckets_to_generate) < num_records:
        buckets_to_generate.append(SOFT_DECLINE) # Add to the largest bucket
    while len(buckets_to_generate) > num_records:
        buckets_to_generate.pop()

    rng.shuffle(buckets_to_generate)

    records = []
    for i, bucket in enumerate(buckets_to_generate, start=1):
        record = generate_record(i, bucket, base_time, rng)
        records.append(record)
        
    return records


def save_dataset(output_dir: Path = Path("data"), seed: int = 42) -> None:
    """
    Generates and saves the dataset to data/dataset.json.
    Does NOT produce a splits.json (bootstrap design — no fixed split).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    records = generate_dataset(seed=seed)
    
    out_path = output_dir / "dataset.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)


def get_ground_truth_outcome(
    payment_id: int,
    action: str,
) -> tuple[bool, float, int]:
    """
    Looks up the pre-computed ground-truth outcome for a given payment and action.

    This is the ONLY function app/ code should call from this module.
    Returns: (recovered: bool, amount_recovered: float, attempts_used: int)
    """
    if not _DATASET_CACHE:
        dataset_path = Path("data/dataset.json")
        if not dataset_path.exists():
            raise FileNotFoundError("Dataset not found. Run generate_dataset first.")
        with open(dataset_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Create index by record id
            for rec in data:
                _DATASET_CACHE[rec["id"]] = rec
                
    record = _DATASET_CACHE.get(payment_id)
    if not record:
        raise ValueError(f"Record ID {payment_id} not found in ground truth dataset.")
        
    outcome_dict = record["ground_truth_outcomes"].get(action)
    if not outcome_dict:
        raise ValueError(f"Action '{action}' is invalid or has no ground truth.")
        
    return (
        outcome_dict["recovered"],
        outcome_dict["amount_recovered"],
        outcome_dict["attempts_used"]
    )


if __name__ == "__main__":
    save_dataset()
