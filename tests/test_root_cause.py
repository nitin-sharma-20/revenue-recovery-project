"""
Unit tests for deterministic root cause classification.
Tests all 5 taxonomy buckets (hard_decline, soft_decline, insufficient_funds, network_error, risky)
across known error codes and keyword fallbacks.
"""

import pytest
from app.root_cause import (
    classify_failure_by_rule,
    HARD_DECLINE,
    SOFT_DECLINE,
    INSUFFICIENT_FUNDS,
    NETWORK_ERROR,
    RISKY,
    UNKNOWN
)


def test_classify_hard_decline_codes():
    bucket, classified_by, reason = classify_failure_by_rule("BAD_REQUEST_PAYMENT_CARD_EXPIRED", "")
    assert bucket == HARD_DECLINE
    assert classified_by == "rule"

    bucket, _, _ = classify_failure_by_rule("GATEWAY_ERROR_CARD_STOLEN", "")
    assert bucket == HARD_DECLINE

    bucket, _, _ = classify_failure_by_rule("BAD_REQUEST_PAYMENT_CARD_INVALID", "")
    assert bucket == HARD_DECLINE


def test_classify_risky_codes_and_keywords():
    bucket, _, _ = classify_failure_by_rule("BAD_REQUEST_PAYMENT_RISK_CHECK_FAILED", "")
    assert bucket == RISKY

    bucket, _, _ = classify_failure_by_rule("GATEWAY_ERROR", "Transaction flagged for fraud suspicion by Thirdwatch")
    assert bucket == RISKY


def test_classify_insufficient_funds():
    bucket, _, _ = classify_failure_by_rule("BAD_REQUEST_PAYMENT_ACCOUNT_INSUFFICIENT_BALANCE", "")
    assert bucket == INSUFFICIENT_FUNDS

    bucket, _, _ = classify_failure_by_rule(None, "Customer account does not have enough balance")
    assert bucket == INSUFFICIENT_FUNDS


def test_classify_network_error():
    bucket, _, _ = classify_failure_by_rule("GATEWAY_ERROR_PAYMENT_TIMED_OUT", "")
    assert bucket == NETWORK_ERROR

    bucket, _, _ = classify_failure_by_rule("GATEWAY_ERROR", "Connection reset: switch unavailable")
    assert bucket == NETWORK_ERROR


def test_classify_soft_decline():
    bucket, _, _ = classify_failure_by_rule("BAD_REQUEST_PAYMENT_DECLINED_BY_BANK", "")
    assert bucket == SOFT_DECLINE

    bucket, _, _ = classify_failure_by_rule("GATEWAY_ERROR_DO_NOT_HONOR", "")
    assert bucket == SOFT_DECLINE


def test_unrecognized_fallback():
    # Unrecognized code should safely fall back to distinct 'unknown' bucket for human review
    bucket, classified_by, reason = classify_failure_by_rule("STRANGE_CUSTOM_CODE_123", "Some unspecified bank issue")
    assert bucket == UNKNOWN
    assert classified_by == "rule"
    assert "Classified as 'unknown' for human review" in reason
