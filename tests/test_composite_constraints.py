import pytest
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from app.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import PaymentEvent, Decision, Outcome


def test_unique_composite_constraint_decision():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    event = PaymentEvent(
        razorpay_payment_id="pay_123",
        amount=100.0,
        currency="INR",
        created_at=datetime.now(timezone.utc)
    )
    db.add(event)
    db.commit()

    # First write
    decision1 = Decision(
        event_id=event.id,
        condition="baseline_a",
        run_id="run_1",
        recommended_action="retry_now",
        reasoning="first"
    )
    db.add(decision1)
    db.commit()

    # Second write for same event_id, condition, run_id -> MUST FAIL
    decision2 = Decision(
        event_id=event.id,
        condition="baseline_a",
        run_id="run_1",
        recommended_action="escalate_human",
        reasoning="second"
    )
    db.add(decision2)
    with pytest.raises(IntegrityError):
        db.commit()


def test_unique_composite_constraint_outcome():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    event = PaymentEvent(
        razorpay_payment_id="pay_123",
        amount=100.0,
        currency="INR",
        created_at=datetime.now(timezone.utc)
    )
    db.add(event)
    db.commit()

    # First write
    outcome1 = Outcome(
        event_id=event.id,
        condition="baseline_a",
        run_id="run_1",
        recovered=True,
        amount_recovered=100.0,
        attempts_used=1
    )
    db.add(outcome1)
    db.commit()

    # Second write for same event_id, condition, run_id -> MUST FAIL
    outcome2 = Outcome(
        event_id=event.id,
        condition="baseline_a",
        run_id="run_1",
        recovered=False,
        amount_recovered=0.0,
        attempts_used=1
    )
    db.add(outcome2)
    with pytest.raises(IntegrityError):
        db.commit()

if __name__ == "__main__":
    pytest.main(["-v", __file__])
