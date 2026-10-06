"""
app/models.py — SQLAlchemy ORM models for Reclaim.

Schema version: v2 (Rebuild)
Changes from v1:
  - PaymentEvent: added instrument_type, dataset_version
  - Decision: added run_id, condition, recommendation_source
  - Outcome: added run_id, condition (NO fold_id — see DESIGN_DECISIONS.md item 12)
  - decisions: composite UNIQUE (event_id, condition, run_id)
  - outcomes:  composite UNIQUE (event_id, condition, run_id)
  - PolicyVerdict: added INSTRUMENT_INELIGIBLE to rejection_rule enum values
  - EvaluationRun: new table for reproducibility logging (DESIGN_DECISIONS.md item 14)
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from app.db import Base


def utc_now():
    return datetime.now(timezone.utc)


class PaymentEvent(Base):
    __tablename__ = "payment_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    razorpay_payment_id = Column(String(100), index=True, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(10), default="INR", nullable=False)
    failure_reason_raw = Column(Text, nullable=True)
    failure_reason_code = Column(String(100), nullable=True)
    customer_id = Column(String(100), nullable=True)
    order_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    split_bucket = Column(String(20), nullable=True)   # legacy field, kept for compatibility

    # v2 additions (DESIGN_DECISIONS.md items 3 and 6)
    instrument_type = Column(String(30), nullable=True)    # "mandate" | "one_off_card"
    dataset_version = Column(String(20), nullable=True)    # e.g. "v2"

    # Relationships
    classifications = relationship(
        "RootCauseClassification", back_populates="event", cascade="all, delete-orphan"
    )
    decisions = relationship(
        "Decision", back_populates="event", cascade="all, delete-orphan"
    )
    outcomes = relationship(
        "Outcome", back_populates="event", cascade="all, delete-orphan"
    )


class RootCauseClassification(Base):
    __tablename__ = "root_cause_classifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey("payment_events.id"), nullable=False, unique=True, index=True)
    bucket = Column(
        String(50), nullable=False
    )  # hard_decline / soft_decline / insufficient_funds / network_error / risky / unknown
    classified_by = Column(String(20), nullable=False)  # rule / llm
    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationships
    event = relationship("PaymentEvent", back_populates="classifications")


class Decision(Base):
    __tablename__ = "decisions"

    # COMPOSITE UNIQUE CONSTRAINT (DESIGN_DECISIONS.md item 7):
    # One decision per (event, condition, run_id) — prevents cross-run contamination.
    __table_args__ = (
        UniqueConstraint("event_id", "condition", "run_id", name="uq_decision_event_condition_run"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey("payment_events.id"), nullable=False, index=True)
    strategy = Column(String(20), nullable=True)   # legacy: "A" / "B" / "C" (kept for old data)

    # v2 replacements for strategy field
    condition = Column(
        String(20), nullable=True
    )  # "baseline_a" / "flag_off" / "flag_on"
    run_id = Column(String(36), nullable=True)     # UUID4 per evaluation run

    recommended_action = Column(
        String(50), nullable=False
    )  # retry_now / retry_later / switch_method / escalate_human / stop
    reasoning = Column(Text, nullable=False)

    # v2 addition: explicit source tracking (DESIGN_DECISIONS.md item 10)
    recommendation_source = Column(
        String(30), nullable=True
    )  # "llm_openai" | "llm_groq" | "heuristic" | "llm_fallback_all_failed"

    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationships
    event = relationship("PaymentEvent", back_populates="decisions")
    verdict = relationship(
        "PolicyVerdict", back_populates="decision", uselist=False, cascade="all, delete-orphan"
    )
    actions = relationship(
        "ActionTaken", back_populates="decision", cascade="all, delete-orphan"
    )


class PolicyVerdict(Base):
    __tablename__ = "policy_verdicts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    decision_id = Column(
        Integer, ForeignKey("decisions.id"), nullable=False, unique=True, index=True
    )
    allowed = Column(Boolean, nullable=False)
    reason = Column(Text, nullable=False)

    # Structured rejection rule (DESIGN_DECISIONS.md item 7):
    # BUCKET_BLOCKED | AGE_CUTOFF_EXCEEDED | RETRY_CAP_EXCEEDED |
    # BACKOFF_WINDOW_VIOLATED | INVALID_ACTION | INSTRUMENT_INELIGIBLE | None (approved)
    rejection_rule = Column(String(50), nullable=True)

    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationships
    decision = relationship("Decision", back_populates="verdict")


class ActionTaken(Base):
    __tablename__ = "actions_taken"

    id = Column(Integer, primary_key=True, autoincrement=True)
    decision_id = Column(Integer, ForeignKey("decisions.id"), nullable=False, index=True)
    action_type = Column(String(50), nullable=False)

    # Idempotency key derived from (event_id, condition, run_id, attempt_number)
    # so cross-run collisions are impossible by construction (DESIGN_DECISIONS.md item 7)
    idempotency_key = Column(String(200), unique=True, index=True, nullable=False)

    executed_at = Column(DateTime, default=utc_now, nullable=False)
    razorpay_response = Column(Text, nullable=True)

    # Relationships
    decision = relationship("Decision", back_populates="actions")


class Outcome(Base):
    __tablename__ = "outcomes"

    # COMPOSITE UNIQUE CONSTRAINT (DESIGN_DECISIONS.md item 7):
    # One outcome per (event, condition, run_id).
    # NO fold_id column — bootstrap resampling is statistics-only, no DB writes per draw.
    # (DESIGN_DECISIONS.md item 12 correction)
    __table_args__ = (
        UniqueConstraint("event_id", "condition", "run_id", name="uq_outcome_event_condition_run"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey("payment_events.id"), nullable=False, index=True)
    strategy = Column(String(20), nullable=True)   # legacy field, kept for old data

    # v2 additions
    condition = Column(String(20), nullable=True)   # "baseline_a" / "flag_off" / "flag_on"
    run_id = Column(String(36), nullable=True)

    recovered = Column(Boolean, nullable=False)
    amount_recovered = Column(Float, default=0.0, nullable=False)
    attempts_used = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationships
    event = relationship("PaymentEvent", back_populates="outcomes")


class EvaluationRun(Base):
    """
    Reproducibility log for every evaluation run (DESIGN_DECISIONS.md item 14).
    Written at the start of eval/run_evaluation.py, before any strategy executes.
    Also written to eval/runs/<run_id>.json for DB-reset resilience.
    """
    __tablename__ = "evaluation_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(36), unique=True, nullable=False, index=True)
    git_commit = Column(String(40), nullable=True)   # git rev-parse HEAD
    config_json = Column(Text, nullable=True)        # JSON: flags, model, seed, dataset_version, n_bootstrap
    seed = Column(Integer, nullable=True)
    dataset_version = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
