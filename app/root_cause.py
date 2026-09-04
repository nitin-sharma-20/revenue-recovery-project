"""
Root Cause Classification Module.
Classifies payment failures into one of 6 distinct taxonomy buckets:
- hard_decline: permanently invalid card/account (never retry)
- soft_decline: temporary bank/issuer decline (retry with backoff)
- insufficient_funds: account balance insufficient (retry later)
- network_error: network/gateway timeout (safe to retry soon)
- risky: flagged by risk engine/fraud check (never auto-retry, escalate to human)
- unknown: unrecognized error code/description (never auto-retry, escalate to human review)
"""

from typing import Tuple, Optional
from sqlalchemy.orm import Session
from app.models import PaymentEvent, RootCauseClassification

# Taxonomy constants
HARD_DECLINE = "hard_decline"
SOFT_DECLINE = "soft_decline"
INSUFFICIENT_FUNDS = "insufficient_funds"
NETWORK_ERROR = "network_error"
RISKY = "risky"
UNKNOWN = "unknown"

ALL_BUCKETS = {HARD_DECLINE, SOFT_DECLINE, INSUFFICIENT_FUNDS, NETWORK_ERROR, RISKY, UNKNOWN}

# Known Razorpay error codes mapping
CODE_MAPPINGS = {
    # Hard Declines
    "BAD_REQUEST_PAYMENT_CARD_EXPIRED": HARD_DECLINE,
    "BAD_REQUEST_PAYMENT_CARD_INVALID": HARD_DECLINE,
    "BAD_REQUEST_PAYMENT_CARD_INACTIVE": HARD_DECLINE,
    "BAD_REQUEST_CARD_INACTIVE": HARD_DECLINE,
    "GATEWAY_ERROR_CARD_STOLEN": HARD_DECLINE,
    "GATEWAY_ERROR_CARD_RESTRICTED": HARD_DECLINE,
    "GATEWAY_ERROR_LOST_CARD": HARD_DECLINE,
    "GATEWAY_ERROR_PICKUP_CARD": HARD_DECLINE,
    "GATEWAY_ERROR_CARD_EXPIRED": HARD_DECLINE,
    "BAD_REQUEST_PAYMENT_ACCOUNT_CLOSED": HARD_DECLINE,

    # Risky / Fraud
    "BAD_REQUEST_PAYMENT_RISK_CHECK_FAILED": RISKY,
    "BAD_REQUEST_PAYMENT_POSSIBLE_FRAUD": RISKY,
    "GATEWAY_ERROR_FRAUD_SUSPECTED": RISKY,
    "BAD_REQUEST_SUSPICIOUS_PAYMENT": RISKY,
    "RISK_THRESHOLD_EXCEEDED": RISKY,
    "BAD_REQUEST_PAYMENT_BLOCKED_BY_RISK": RISKY,

    # Insufficient Funds
    "BAD_REQUEST_PAYMENT_ACCOUNT_INSUFFICIENT_BALANCE": INSUFFICIENT_FUNDS,
    "GATEWAY_ERROR_INSUFFICIENT_FUNDS": INSUFFICIENT_FUNDS,
    "BAD_REQUEST_INSUFFICIENT_FUNDS": INSUFFICIENT_FUNDS,
    "GATEWAY_ERROR_LOW_BALANCE": INSUFFICIENT_FUNDS,

    # Network / Timeout Errors
    "GATEWAY_ERROR_PAYMENT_TIMED_OUT": NETWORK_ERROR,
    "BAD_REQUEST_PAYMENT_TIMED_OUT": NETWORK_ERROR,
    "GATEWAY_ERROR_NETWORK_FAILURE": NETWORK_ERROR,
    "GATEWAY_ERROR_CONNECTION_TIMEOUT": NETWORK_ERROR,
    "BAD_REQUEST_GATEWAY_TIMEOUT": NETWORK_ERROR,
    "GATEWAY_ERROR_ISSUER_UNAVAILABLE": NETWORK_ERROR,
    "GATEWAY_ERROR_COMMUNICATION_ERROR": NETWORK_ERROR,
    "GATEWAY_ERROR_BANK_SYSTEM_DOWN": NETWORK_ERROR,

    # Soft Declines
    "BAD_REQUEST_PAYMENT_DECLINED_BY_BANK": SOFT_DECLINE,
    "GATEWAY_ERROR_DEBIT_FAILED": SOFT_DECLINE,
    "GATEWAY_ERROR_DO_NOT_HONOR": SOFT_DECLINE,
    "GATEWAY_ERROR_TRANSACTION_NOT_PERMITTED": SOFT_DECLINE,
    "GATEWAY_ERROR_PAYMENT_FAILED": SOFT_DECLINE,
    "BAD_REQUEST_PAYMENT_DECLINED": SOFT_DECLINE,
    "GATEWAY_ERROR_AUTH_FAILED": SOFT_DECLINE,
    "BAD_REQUEST_PAYMENT_AUTH_FAILED": SOFT_DECLINE,
    "GATEWAY_ERROR_TRY_AGAIN_LATER": SOFT_DECLINE
}

# Substring / Keyword matching for unstructured error descriptions
KEYWORD_MAPPINGS = [
    # Risky first to avoid accidental soft matches
    (RISKY, ["fraud", "suspicious", "risk check", "risk_check", "security violation", "thirdwatch", "blocked by risk", "risky"]),
    # Hard declines
    (HARD_DECLINE, ["expired", "stolen", "lost card", "restricted", "invalid card", "card closed", "account closed", "pickup card", "hard decline", "inactive card"]),
    # Insufficient funds
    (INSUFFICIENT_FUNDS, ["insufficient balance", "insufficient funds", "insufficient", "not enough balance", "enough balance", "low balance", "exceeds balance"]),
    # Network errors
    (NETWORK_ERROR, ["timed out", "timeout", "timed_out", "network failure", "connection reset", "connection refused", "switch unavailable", "system down", "communication failure", "transport error", "gateway timeout"]),
    # Soft declines
    (SOFT_DECLINE, ["declined by bank", "declined", "do not honor", "transaction not permitted", "debit failed", "auth failed", "try again", "temporary failure", "soft decline"])
]


def classify_failure_by_rule(
    error_code: Optional[str],
    error_description: Optional[str]
) -> Tuple[str, str, str]:
    """
    Deterministically classifies a payment failure using error code or text heuristics.
    Returns: (bucket, classified_by, reasoning)
    """
    code = (error_code or "").strip().upper()
    desc = (error_description or "").strip().lower()

    # 1. Exact code match
    if code in CODE_MAPPINGS:
        bucket = CODE_MAPPINGS[code]
        return bucket, "rule", f"Matched known Razorpay error code: '{code}'"

    # 2. Text heuristics match on description
    for bucket, keywords in KEYWORD_MAPPINGS:
        for kw in keywords:
            if kw in desc:
                return bucket, "rule", f"Matched failure description keyword: '{kw}'"

    # 3. Default fallback for generic/unrecognized failures
    # Unclassified/unrecognized gateway errors are classified into a distinct 'unknown' bucket
    # to mandate manual review (escalate_human) and prohibit automated retries.
    return UNKNOWN, "rule", f"Unrecognized code '{code}' and description. Classified as 'unknown' for human review"


def classify_and_persist(event: PaymentEvent, db: Session) -> RootCauseClassification:
    """
    Classifies a PaymentEvent and persists the classification to the database.
    """
    bucket, classified_by, _ = classify_failure_by_rule(
        event.failure_reason_code,
        event.failure_reason_raw
    )

    classification = RootCauseClassification(
        event_id=event.id,
        bucket=bucket,
        classified_by=classified_by
    )
    db.add(classification)
    db.commit()
    db.refresh(classification)
    return classification
