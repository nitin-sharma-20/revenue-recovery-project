from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    Text
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
    split_bucket = Column(String(20), nullable=True)  # dev / validation / held_out

    # Relationships
    classifications = relationship("RootCauseClassification", back_populates="event", cascade="all, delete-orphan")
    decisions = relationship("Decision", back_populates="event", cascade="all, delete-orphan")
    outcomes = relationship("Outcome", back_populates="event", cascade="all, delete-orphan")


class RootCauseClassification(Base):
    __tablename__ = "root_cause_classifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey("payment_events.id"), nullable=False, index=True)
    bucket = Column(String(50), nullable=False)  # hard_decline / soft_decline / insufficient_funds / network_error / risky / unknown
    classified_by = Column(String(20), nullable=False)  # rule / llm
    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationships
    event = relationship("PaymentEvent", back_populates="classifications")


class Decision(Base):
    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey("payment_events.id"), nullable=False, index=True)
    strategy = Column(String(20), nullable=False)  # A / B / C
    recommended_action = Column(String(50), nullable=False)  # retry_now / retry_later / switch_method / escalate_human / stop
    reasoning = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationships
    event = relationship("PaymentEvent", back_populates="decisions")
    verdict = relationship("PolicyVerdict", back_populates="decision", uselist=False, cascade="all, delete-orphan")
    actions = relationship("ActionTaken", back_populates="decision", cascade="all, delete-orphan")


class PolicyVerdict(Base):
    __tablename__ = "policy_verdicts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    decision_id = Column(Integer, ForeignKey("decisions.id"), nullable=False, unique=True, index=True)
    allowed = Column(Boolean, nullable=False)
    reason = Column(Text, nullable=False)
    rejection_rule = Column(String(50), nullable=True)  # Structured: BUCKET_BLOCKED / AGE_CUTOFF_EXCEEDED / RETRY_CAP_EXCEEDED / BACKOFF_WINDOW_VIOLATED / INVALID_ACTION / None if approved
    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationships
    decision = relationship("Decision", back_populates="verdict")


class ActionTaken(Base):
    __tablename__ = "actions_taken"

    id = Column(Integer, primary_key=True, autoincrement=True)
    decision_id = Column(Integer, ForeignKey("decisions.id"), nullable=False, index=True)
    action_type = Column(String(50), nullable=False)
    idempotency_key = Column(String(150), unique=True, index=True, nullable=False)
    executed_at = Column(DateTime, default=utc_now, nullable=False)
    razorpay_response = Column(Text, nullable=True)

    # Relationships
    decision = relationship("Decision", back_populates="actions")


class Outcome(Base):
    __tablename__ = "outcomes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey("payment_events.id"), nullable=False, index=True)
    strategy = Column(String(20), nullable=False)  # A / B / C
    recovered = Column(Boolean, nullable=False)
    amount_recovered = Column(Float, default=0.0, nullable=False)
    attempts_used = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationships
    event = relationship("PaymentEvent", back_populates="outcomes")
