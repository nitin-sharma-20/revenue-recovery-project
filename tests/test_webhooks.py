import hmac
import hashlib
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db import Base, get_db
from app.config import settings
from app.models import PaymentEvent

# Use an in-memory SQLite database with StaticPool so all sessions share the same memory instance
TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


def generate_signature(payload_bytes: bytes, secret: str) -> str:
    return hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256
    ).hexdigest()


def test_webhook_payment_failed_success():
    """
    Acceptance Test: A test webhook payload with valid signature can be POSTed
    and appears correctly in payment_events.
    """
    payload = {
        "entity": "event",
        "account_id": "acc_test123",
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_test_001",
                    "entity": "payment",
                    "amount": 49900,  # 499.00 INR in paise
                    "currency": "INR",
                    "status": "failed",
                    "order_id": "order_test_999",
                    "customer_id": "cust_test_456",
                    "error_code": "BAD_REQUEST_PAYMENT_DECLINED_BY_BANK",
                    "error_description": "Payment was declined by bank due to insufficient funds.",
                    "error_source": "bank",
                    "error_step": "payment_authorization",
                    "error_reason": "payment_declined"
                }
            }
        },
        "created_at": 1600000000
    }

    body_bytes = json.dumps(payload).encode("utf-8")
    signature = generate_signature(body_bytes, settings.RAZORPAY_WEBHOOK_SECRET)

    response = client.post(
        "/webhook/razorpay",
        content=body_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": signature
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "recorded"
    assert data["payment_id"] == "pay_test_001"
    assert data["amount"] == 499.0

    # Verify directly in the database
    db = TestingSessionLocal()
    event = db.query(PaymentEvent).filter_by(razorpay_payment_id="pay_test_001").first()
    assert event is not None
    assert event.razorpay_payment_id == "pay_test_001"
    assert event.amount == 499.0
    assert event.currency == "INR"
    assert event.failure_reason_code == "BAD_REQUEST_PAYMENT_DECLINED_BY_BANK"
    assert "insufficient funds" in event.failure_reason_raw
    assert event.customer_id == "cust_test_456"
    assert event.order_id == "order_test_999"
    assert event.split_bucket == "dev"
    db.close()


def test_webhook_invalid_signature_rejected():
    """Verify that a webhook with an invalid signature is rejected with 400 Bad Request."""
    payload = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_tampered_002",
                    "amount": 10000,
                    "currency": "INR",
                    "error_code": "BAD_REQUEST"
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/webhook/razorpay",
        content=body_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": "invalid_fake_signature"
        }
    )

    assert response.status_code == 400
    assert "Invalid webhook signature" in response.json()["detail"]

    # Verify no row was written to the database
    db = TestingSessionLocal()
    event = db.query(PaymentEvent).filter_by(razorpay_payment_id="pay_tampered_002").first()
    assert event is None
    db.close()


def test_webhook_non_failed_event_ignored():
    """Verify that non-failed payment events are ignored safely without errors."""
    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_captured_003",
                    "amount": 25000,
                    "currency": "INR"
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    signature = generate_signature(body_bytes, settings.RAZORPAY_WEBHOOK_SECRET)

    response = client.post(
        "/webhook/razorpay",
        content=body_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": signature
        }
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"

    # Verify not in DB
    db = TestingSessionLocal()
    event = db.query(PaymentEvent).filter_by(razorpay_payment_id="pay_captured_003").first()
    assert event is None
    db.close()
