"""PaymentIntent state machine (design §5.7). Every transition checks `ALLOWED_FROM` while
holding the PI's row lock."""

from app.errors import ApiError
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


def require_status(pi, action: str) -> None:
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
