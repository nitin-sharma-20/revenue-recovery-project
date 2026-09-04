import hmac
import hashlib
import json
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException, Depends, Header
from sqlalchemy.orm import Session
from app.config import settings
from app.db import get_db
from app.models import PaymentEvent

router = APIRouter(prefix="/webhook", tags=["webhooks"])


def verify_razorpay_signature(body_bytes: bytes, signature: str, secret: str) -> bool:
    """
    Verifies the Razorpay webhook HMAC SHA256 signature.
    """
    if not signature or not secret:
        return False
    generated_signature = hmac.new(
        key=secret.encode("utf-8"),
        msg=body_bytes,
        digestmod=hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(generated_signature, signature)


@router.post("/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(None, alias="X-Razorpay-Signature"),
    db: Session = Depends(get_db)
):
    """
    Ingests Razorpay webhook events.
    Verifies HMAC SHA256 signature and records 'payment.failed' events.
    """
    body_bytes = await request.body()
    
    # Verify HMAC signature
    if not x_razorpay_signature or not verify_razorpay_signature(body_bytes, x_razorpay_signature, settings.RAZORPAY_WEBHOOK_SECRET):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")
    
    try:
        data = json.loads(body_bytes.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {str(e)}")

    event_type = data.get("event")
    
    # MVP scope handles payment.failed events
    if event_type != "payment.failed":
        return {
            "status": "ignored",
            "reason": f"Event '{event_type}' is outside MVP scope (payment.failed only)"
        }

    payload_data = data.get("payload", {})
    payment_entity = payload_data.get("payment", {}).get("entity", {})
    
    razorpay_payment_id = payment_entity.get("id")
    if not razorpay_payment_id:
        raise HTTPException(status_code=400, detail="Missing payment entity ID in webhook payload")

    # In Razorpay, amount is in subunit (paise for INR, e.g. 50000 = 500.00 INR)
    raw_amount = payment_entity.get("amount", 0)
    amount_inr = float(raw_amount) / 100.0 if raw_amount else 0.0

    currency = payment_entity.get("currency", "INR")
    failure_reason_raw = payment_entity.get("error_description") or payment_entity.get("error_reason") or "Unknown failure"
    failure_reason_code = payment_entity.get("error_code") or "UNKNOWN_ERROR"
    customer_id = payment_entity.get("customer_id") or payment_entity.get("email")
    order_id = payment_entity.get("order_id")

    payment_event = PaymentEvent(
        razorpay_payment_id=razorpay_payment_id,
        amount=amount_inr,
        currency=currency,
        failure_reason_raw=failure_reason_raw,
        failure_reason_code=failure_reason_code,
        customer_id=customer_id,
        order_id=order_id,
        split_bucket="dev",  # Ingested live events default to dev split
        created_at=datetime.now(timezone.utc)
    )

    db.add(payment_event)
    db.commit()
    db.refresh(payment_event)

    return {
        "status": "recorded",
        "event_id": payment_event.id,
        "payment_id": payment_event.razorpay_payment_id,
        "amount": payment_event.amount,
        "failure_reason_code": payment_event.failure_reason_code
    }
