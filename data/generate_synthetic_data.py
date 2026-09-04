"""
Synthetic Data Generation for Reclaim Revenue Recovery Engine.
Generates 100+ realistic payment.failed records matching Razorpay webhook schema
with realistic root-cause distributions, amounts, and INDEPENDENT ground-truth outcomes.
Splits data into dev (80%), validation (10%), and held_out (10%).
"""

import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any

# Root cause buckets per GEMINI.md Section 6
# Note: 'unknown' represents genuinely unrecognized gateway error codes that the
# classifier cannot map to any known bucket. These are rare in real traffic but
# must be represented in the dataset so the unknown->escalate_human path is
# exercised end-to-end.
BUCKET_DISTRIBUTION = {
    "soft_decline": 0.33,        # 33% - temporary issuer declines, do-not-honor
    "insufficient_funds": 0.28,  # 28% - account balance issues
    "network_error": 0.14,       # 14% - timeouts, gateway connection drops
    "hard_decline": 0.11,        # 11% - expired/blocked/stolen card, never retry
    "risky": 0.09,               # 9%  - fraud flags, suspicious behavior
    "unknown": 0.05              # 5%  - genuinely unrecognized error codes, human review
}

FAILURE_SCENARIOS = {
    "soft_decline": [
        ("BAD_REQUEST_PAYMENT_DECLINED_BY_BANK", "Payment was declined by the issuing bank."),
        ("GATEWAY_ERROR_DO_NOT_HONOR", "Issuer returned do not honor status code 05."),
        ("GATEWAY_ERROR_TRANSACTION_NOT_PERMITTED", "Transaction not permitted to cardholder by issuer."),
        ("GATEWAY_ERROR_DEBIT_FAILED", "Direct debit attempt failed at acquiring bank."),
        ("BAD_REQUEST_PAYMENT_AUTH_FAILED", "Customer 3D-Secure authentication failed or timed out.")
    ],
    "insufficient_funds": [
        ("BAD_REQUEST_PAYMENT_ACCOUNT_INSUFFICIENT_BALANCE", "Payment failed due to insufficient funds in customer account."),
        ("GATEWAY_ERROR_INSUFFICIENT_FUNDS", "Declined: not enough balance in account."),
        ("BAD_REQUEST_INSUFFICIENT_FUNDS", "Customer account does not have sufficient balance for this charge.")
    ],
    "network_error": [
        ("GATEWAY_ERROR_PAYMENT_TIMED_OUT", "Connection to bank gateway timed out after 30000ms."),
        ("BAD_REQUEST_PAYMENT_TIMED_OUT", "Payment processing session timed out during authorization."),
        ("GATEWAY_ERROR_NETWORK_FAILURE", "Underlying network transport error while communicating with gateway."),
        ("GATEWAY_ERROR_ISSUER_UNAVAILABLE", "Issuing bank switch is currently unavailable/down.")
    ],
    "hard_decline": [
        ("BAD_REQUEST_PAYMENT_CARD_EXPIRED", "The card expiry date is in the past."),
        ("BAD_REQUEST_PAYMENT_CARD_INVALID", "The card number provided is invalid or inactive."),
        ("GATEWAY_ERROR_CARD_STOLEN", "Card reported as lost or stolen by cardholder."),
        ("GATEWAY_ERROR_CARD_RESTRICTED", "Card is restricted or permanently blocked by issuer.")
    ],
    "risky": [
        ("BAD_REQUEST_PAYMENT_RISK_CHECK_FAILED", "Transaction flagged by Razorpay Thirdwatch risk engine."),
        ("BAD_REQUEST_PAYMENT_POSSIBLE_FRAUD", "High risk score detected: rapid successive attempts from foreign IP."),
        ("GATEWAY_ERROR_FRAUD_SUSPECTED", "Issuer fraud detection rule triggered for this merchant.")
    ],
    # Unknown: deliberately unrecognized codes/descriptions that won't match any
    # rule in root_cause.py's CODE_MAPPINGS or KEYWORD_MAPPINGS, so the classifier
    # falls through to the 'unknown' fallback bucket.
    "unknown": [
        ("ISSUER_CUSTOM_REJECT_7741", "Acquirer returned non-standard reject code 7741."),
        ("BANK_INTERNAL_PROC_ERROR_99", "Internal processing exception at partner bank layer."),
        ("PAYMENT_CHANNEL_CLOSED_MAINT", "Payment channel undergoing scheduled maintenance window."),
        ("MERCHANT_AGGREGATOR_FAILOVER", "Aggregator upstream failover triggered for merchant route."),
        ("GATEWAY_UNDOCUMENTED_STATUS_X3", "Gateway returned undocumented status code X3 for auth request."),
        ("SCHEME_CONFIG_MISMATCH_BIN", "Card BIN range not configured in current scheme routing table."),
        ("PROCESSOR_SETTLEMENT_HOLD_41", "Processor placed settlement hold type 41 on this transaction.")
    ]
}


def compute_ground_truth_outcomes(bucket: str, is_unrecoverable: bool, amount: float, rng: random.Random) -> Dict[str, Any]:
    """
    Computes fixed, independent ground-truth outcomes for every possible action.
    This outcome is completely separate from any strategy's decision logic.
    """
    outcomes = {
        "stop": {
            "recovered": False,
            "amount_recovered": 0.0,
            "attempts_used": 0
        },
        "escalate_human": {
            "recovered": False,
            "amount_recovered": 0.0,
            "attempts_used": 0
        }
    }

    if is_unrecoverable or bucket in ["hard_decline", "risky", "unknown"]:
        # Hard decline, risky, and unknown are treated as unrecoverable via automated retries.
        # For 'unknown', the policy engine blocks retries anyway, so ground truth reflects that
        # automated retries would fail even if attempted (we can't know the underlying cause).
        outcomes["retry_now"] = {"recovered": False, "amount_recovered": 0.0, "attempts_used": 1}
        outcomes["retry_later"] = {"recovered": False, "amount_recovered": 0.0, "attempts_used": 2}
        
        # In hard decline, switching payment method (e.g. to UPI/Netbanking) can recover 50% of the time
        if bucket == "hard_decline" and not is_unrecoverable and rng.random() < 0.50:
            outcomes["switch_method"] = {"recovered": True, "amount_recovered": amount, "attempts_used": 1}
        # Unknown: switching method has a low (30%) chance since we don't understand the failure
        elif bucket == "unknown" and rng.random() < 0.30:
            outcomes["switch_method"] = {"recovered": True, "amount_recovered": amount, "attempts_used": 1}
        else:
            outcomes["switch_method"] = {"recovered": False, "amount_recovered": 0.0, "attempts_used": 1}
            
        return outcomes

    # Recoverable payments by bucket:
    if bucket == "network_error":
        # Immediate retry on network glitch succeeds 90% in 1 attempt
        rec_now = rng.random() < 0.90
        outcomes["retry_now"] = {"recovered": rec_now, "amount_recovered": amount if rec_now else 0.0, "attempts_used": 1}
        # Delayed retry succeeds only 50% due to cart abandonment over 24h
        rec_later = rng.random() < 0.50
        outcomes["retry_later"] = {"recovered": rec_later, "amount_recovered": amount if rec_later else 0.0, "attempts_used": 1}
        outcomes["switch_method"] = {"recovered": True, "amount_recovered": amount, "attempts_used": 1}

    elif bucket == "insufficient_funds":
        # Immediate single retry on insufficient funds fails 80% (funds not added in minutes)
        rec_now = rng.random() < 0.20
        outcomes["retry_now"] = {"recovered": rec_now, "amount_recovered": amount if rec_now else 0.0, "attempts_used": 1}
        # Multi-attempt delayed backoff retry succeeds 75% across 2 attempts
        rec_later = rng.random() < 0.75
        outcomes["retry_later"] = {"recovered": rec_later, "amount_recovered": amount if rec_later else 0.0, "attempts_used": 2}
        # Switching payment method to another account succeeds 80%
        rec_switch = rng.random() < 0.80
        outcomes["switch_method"] = {"recovered": rec_switch, "amount_recovered": amount if rec_switch else 0.0, "attempts_used": 1}

    elif bucket == "soft_decline":
        # Immediate single retry fails 75% (bank switch still declining)
        rec_now = rng.random() < 0.25
        outcomes["retry_now"] = {"recovered": rec_now, "amount_recovered": amount if rec_now else 0.0, "attempts_used": 1}
        # Multi-attempt backoff retry succeeds 70% across 2 attempts
        rec_later = rng.random() < 0.70
        outcomes["retry_later"] = {"recovered": rec_later, "amount_recovered": amount if rec_later else 0.0, "attempts_used": 2}
        # Switching payment method succeeds 75%
        rec_switch = rng.random() < 0.75
        outcomes["switch_method"] = {"recovered": rec_switch, "amount_recovered": amount if rec_switch else 0.0, "attempts_used": 1}

    return outcomes


def generate_record(index: int, bucket: str, base_time: datetime, rng: random.Random) -> Dict[str, Any]:
    """Generates a single synthetic payment.failed record with independent ground-truth outcomes."""
    payment_id = f"pay_synth_{index:04d}_{uuid.uuid4().hex[:6]}"
    order_id = f"order_synth_{index:04d}_{uuid.uuid4().hex[:6]}"
    customer_id = f"cust_synth_{rng.randint(100, 999)}"
    
    tier = rng.choices(["low", "mid", "high"], weights=[0.50, 0.35, 0.15])[0]
    if tier == "low":
        amount = round(rng.uniform(299.0, 999.0), 2)
    elif tier == "mid":
        amount = round(rng.uniform(1000.0, 4999.0), 2)
    else:
        amount = round(rng.uniform(5000.0, 25000.0), 2)

    scenarios = FAILURE_SCENARIOS[bucket]
    code, desc = rng.choice(scenarios)

    # 10% chance to simulate free-text / non-standard error message
    if rng.random() < 0.10 and bucket in ["soft_decline", "network_error", "insufficient_funds"]:
        code = "GATEWAY_ERROR"
        desc = f"Generic gateway response: {desc.lower()}"

    created_at = base_time - timedelta(
        days=rng.randint(0, 6),
        hours=rng.randint(0, 23),
        minutes=rng.randint(0, 59)
    )

    # Ground truth recoverability characteristics
    if bucket in ["hard_decline", "risky", "unknown"]:
        is_genuinely_unrecoverable = True
    else:
        # 15% of soft_decline, insufficient_funds, network_error are genuinely unrecoverable
        is_genuinely_unrecoverable = rng.random() < 0.15

    # Independent ground-truth outcomes evaluated at generation time
    ground_truth_outcomes = compute_ground_truth_outcomes(
        bucket=bucket,
        is_unrecoverable=is_genuinely_unrecoverable,
        amount=amount,
        rng=rng
    )

    return {
        "id": index,
        "razorpay_payment_id": payment_id,
        "amount": amount,
        "currency": "INR",
        "failure_reason_raw": desc,
        "failure_reason_code": code,
        "customer_id": customer_id,
        "order_id": order_id,
        "created_at": created_at.isoformat(),
        # Ground truth simulation attributes
        "expected_root_cause": bucket,
        "is_genuinely_unrecoverable": is_genuinely_unrecoverable,
        "customer_tier": tier,
        "ground_truth_outcomes": ground_truth_outcomes
    }


def generate_dataset(num_records: int = 150, seed: int = 42) -> tuple[List[Dict[str, Any]], Dict[str, List[str]]]:
    """
    Generates deterministic synthetic dataset and creates 80/10/10 split.
    """
    rng = random.Random(seed)
    base_time = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
    
    records: List[Dict[str, Any]] = []
    current_idx = 1

    for bucket, weight in BUCKET_DISTRIBUTION.items():
        count = int(num_records * weight)
        for _ in range(count):
            rec = generate_record(current_idx, bucket, base_time, rng)
            records.append(rec)
            current_idx += 1

    while len(records) < num_records:
        rec = generate_record(current_idx, "soft_decline", base_time, rng)
        records.append(rec)
        current_idx += 1

    # Shuffle deterministically
    rng.shuffle(records)

    # Assign splits: 80% dev, 10% validation, 10% held_out
    n_total = len(records)
    n_dev = int(n_total * 0.80)
    n_val = int(n_total * 0.10)

    splits = {
        "dev": [],
        "validation": [],
        "held_out": []
    }

    for i, rec in enumerate(records):
        if i < n_dev:
            rec["split_bucket"] = "dev"
            splits["dev"].append(rec["razorpay_payment_id"])
        elif i < n_dev + n_val:
            rec["split_bucket"] = "validation"
            splits["validation"].append(rec["razorpay_payment_id"])
        else:
            rec["split_bucket"] = "held_out"
            splits["held_out"].append(rec["razorpay_payment_id"])

    return records, splits


def save_synthetic_data(output_dir: Path = Path("data")):
    """Saves generated dataset and splits to disk."""
    output_dir.mkdir(parents=True, exist_ok=True)
    records, splits = generate_dataset(num_records=150, seed=42)

    dataset_file = output_dir / "dataset.json"
    splits_file = output_dir / "splits.json"

    with open(dataset_file, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

    with open(splits_file, "w", encoding="utf-8") as f:
        json.dump(splits, f, indent=2)

    print(f"Generated {len(records)} records:")
    print(f"  - Dev: {len(splits['dev'])} records (80%)")
    print(f"  - Validation: {len(splits['validation'])} records (10%)")
    print(f"  - Held-out: {len(splits['held_out'])} records (10%)")
_DATASET_CACHE: Dict[str, Dict[str, Any]] = {}


def get_ground_truth_outcome(payment_id: str, action: str) -> tuple[bool, float, int]:
    """
    Looks up the fixed, independent ground-truth outcome for a given payment ID and action.
    This guarantees that recovery success is determined by the pre-computed ground-truth
    recoverability model, completely independent of strategy logic.
    """
    global _DATASET_CACHE
    if not _DATASET_CACHE:
        dataset_path = Path("data/dataset.json")
        if dataset_path.exists():
            with open(dataset_path, "r", encoding="utf-8") as f:
                records = json.load(f)
                _DATASET_CACHE = {r["razorpay_payment_id"]: r for r in records}

    record = _DATASET_CACHE.get(payment_id)
    if not record:
        return False, 0.0, 0

    gt_outcomes = record.get("ground_truth_outcomes", {})
    action_result = gt_outcomes.get(action)
    if not action_result:
        return False, 0.0, 0

    return (
        action_result["recovered"],
        action_result["amount_recovered"],
        action_result["attempts_used"]
    )


if __name__ == "__main__":
    save_synthetic_data()
