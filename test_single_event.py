import os
import sys
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import PaymentEvent, Decision
from app.llm_recommender import get_llm_recommendation

def main():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    
    event = PaymentEvent(
        razorpay_payment_id="pay_test_001",
        amount=100.0,
        currency="INR",
        failure_reason_raw="Timeout",
        failure_reason_code="TIMEOUT",
        customer_id="cust_001",
        order_id="order_001",
        split_bucket="dev",
        created_at=datetime.now(timezone.utc)
    )
    db.add(event)
    db.flush()
    
    print("Testing LLM Recommendation...")
    rec, source = get_llm_recommendation(
        root_cause_bucket="network_error",
        amount=event.amount,
        error_code=event.failure_reason_code,
        error_description=event.failure_reason_raw,
        previous_attempts=0
    )
    
    print(f"Recommendation: {rec.action.value}")
    print(f"Source: {source}")
    print(f"Reasoning: {rec.reasoning}")
    
if __name__ == "__main__":
    main()
