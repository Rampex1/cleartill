"""PaymentIntent state machine (design §5.7). Every transition checks `ALLOWED_FROM` while
holding the PI's row lock."""

from fastapi import Request
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.events import emit
from app.ids import new_id, random_string
from app.models import PaymentIntent

ALLOWED_FROM: dict[str, set[str]] = {
    "update": {"requires_payment_method", "requires_confirmation"},
    "confirm": {"requires_payment_method", "requires_confirmation", "requires_action"},
    "capture": {"requires_capture"},
    "cancel": {
        "requires_payment_method",
        "requires_confirmation",
        "requires_action",
        "requires_capture",
    },
}


def create_pi(
    db: Session,
    merchant_id: str,
    *,
    amount: int,
    currency: str,
    capture_method: str,
    customer_id: str | None = None,
    payment_method_id: str | None = None,
    return_url: str | None = None,
    metadata: dict | None = None,
    checkout_session_id: str | None = None,
    request: Request | None = None,
) -> PaymentIntent:
    """Create a PI and emit payment_intent.created (used by /v1, confirm=true and checkout)."""
    pi_id = new_id("pi_")
    pi = PaymentIntent(
        id=pi_id,
        merchant_id=merchant_id,
        amount=amount,
        currency=currency,
        status="requires_confirmation" if payment_method_id else "requires_payment_method",
        capture_method=capture_method,
        amount_capturable=0,
        amount_received=0,
        customer_id=customer_id,
        payment_method_id=payment_method_id,
        client_secret=f"{pi_id}_secret_{random_string(24)}",
        return_url=return_url,
        checkout_session_id=checkout_session_id,
        metadata_=metadata or {},
    )
    db.add(pi)
    db.flush()
    emit(db, merchant_id, "payment_intent.created", pi_json(pi), request)
    return pi


def require_status(pi, action: str) -> None:
    # ponytail: M17 also rejects /v1 update/confirm/cancel when pi.checkout_session_id is set.
    if pi.status not in ALLOWED_FROM[action]:
        raise ApiError(
            400,
            "invalid_request_error",
            f"You cannot {action} this PaymentIntent because it has a status of {pi.status}.",
            code="payment_intent_unexpected_state",
        )


def pi_json(pi: PaymentIntent) -> dict:
    return {
        "id": pi.id,
        "object": "payment_intent",
        "amount": pi.amount,
        "amount_capturable": pi.amount_capturable,
        "amount_received": pi.amount_received,
        "currency": pi.currency,
        "status": pi.status,
        "capture_method": pi.capture_method,
        "customer": pi.customer_id,
        "payment_method": pi.payment_method_id,
        "latest_charge": pi.latest_charge_id,
        "client_secret": pi.client_secret,
        "last_payment_error": pi.last_payment_error,
        "next_action": pi.next_action,
        "return_url": pi.return_url,
        "cancellation_reason": pi.cancellation_reason,
        "metadata": pi.metadata_,
        "created": int(pi.created.timestamp()),
        "livemode": False,
    }
