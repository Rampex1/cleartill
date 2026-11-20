"""PaymentIntent state machine (design §5.7). Every transition checks `ALLOWED_FROM` while
holding the PI's row lock."""

from fastapi import Request
from sqlalchemy.orm import Session

from app import cards, clock, config, network
from app.charges import capture_charge, charge_json
from app.errors import ApiError
from app.events import emit
from app.executor import Result
from app.ids import new_id, random_string
from app.models import Charge, PaymentIntent, PaymentMethod
from app.tenancy import get_owned

# Stripe's test PaymentMethod ids: each use creates a real pm_ for the matching test card.
TEST_PAYMENT_METHODS = {
    "pm_card_visa": "4242424242424242",
    "pm_card_visa_chargeDeclined": "4000000000000002",
    "pm_card_visa_chargeDeclinedInsufficientFunds": "4000000000009995",
    "pm_card_authenticationRequired": "4000002760003184",
}

DECLINE_MESSAGES = {
    "card_declined": "Your card was declined.",
    "expired_card": "Your card has expired.",
    "incorrect_cvc": "Your card's security code is incorrect.",
    "processing_error": "An error occurred while processing your card. Try again in a little bit.",
}
INSUFFICIENT_FUNDS_MESSAGE = "Your card has insufficient funds."

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


def resolve_payment_method(
    db: Session, merchant_id: str, value: str | None, param: str = "payment_method"
) -> PaymentMethod | None:
    """A pm_ id of this merchant, or one of Stripe's test ids (creates a real pm_)."""
    if not value:
        return None
    number = TEST_PAYMENT_METHODS.get(value)
    if number is None:
        return get_owned(db, PaymentMethod, value, merchant_id, param=param)
    pm = PaymentMethod(
        id=new_id("pm_"),
        merchant_id=merchant_id,
        brand=cards.brand_for(number),
        last4=number[-4:],
        exp_month=12,
        exp_year=clock.now().year + 5,
        fingerprint=cards.fingerprint(number),
        sim_outcome=cards.sim_outcome_for(number),
    )
    db.add(pm)
    db.flush()
    return pm


def _decline_message(code: str, decline_code: str | None) -> str:
    if decline_code == "insufficient_funds":
        return INSUFFICIENT_FUNDS_MESSAGE
    return DECLINE_MESSAGES.get(code, "Your card was declined.")


def _new_charge(db: Session, pi: PaymentIntent, pm: PaymentMethod, **fields) -> Charge:
    charge = Charge(
        id=new_id("ch_"),
        merchant_id=pi.merchant_id,
        payment_intent_id=pi.id,
        payment_method_id=pm.id,
        amount=pi.amount,
        currency=pi.currency,
        amount_captured=0,
        amount_refunded=0,
        captured=False,
        **fields,
    )
    db.add(charge)
    db.flush()
    pi.latest_charge_id = charge.id
    return charge


def approve(db: Session, pi: PaymentIntent, pm: PaymentMethod, request: Request | None) -> dict:
    """Network approved: a succeeded charge; capture now unless capture_method is manual."""
    charge = _new_charge(db, pi, pm, status="succeeded", authorized_at=clock.now())
    if pi.capture_method == "manual":
        pi.status = "requires_capture"
        pi.amount_capturable = pi.amount
        pi_event = "payment_intent.amount_capturable_updated"
    else:
        capture_charge(db, charge, pi.amount)
        pi.status = "succeeded"
        pi.amount_received = pi.amount
        pi.amount_capturable = 0
        pi_event = "payment_intent.succeeded"
    pi.next_action = None
    db.flush()
    emit(db, pi.merchant_id, "charge.succeeded", charge_json(db, charge), request)
    body = pi_json(pi)
    emit(db, pi.merchant_id, pi_event, body, request)
    return body


def decline(
    db: Session,
    pi: PaymentIntent,
    pm: PaymentMethod,
    request: Request | None,
    *,
    charge_code: str,
    error_code: str | None = None,
    decline_code: str | None = None,
    message: str | None = None,
) -> Result:
    """A failed charge; the PI goes back to requires_payment_method. `error_code` defaults to
    the charge's failure code (3DS failure differs: payment_intent_authentication_failure)."""
    message = message or _decline_message(charge_code, decline_code)
    charge = _new_charge(
        db, pi, pm, status="failed", failure_code=charge_code, failure_message=message
    )
    error = {"type": "card_error", "code": error_code or charge_code, "message": message}
    if decline_code:
        error["decline_code"] = decline_code
    pi.last_payment_error = {**error, "payment_method": cards.payment_method_json(pm)}
    pi.payment_method_id = None  # retry needs a new payment method (unverified vs Stripe, Q6)
    pi.status = "requires_payment_method"
    pi.next_action = None
    db.flush()
    emit(db, pi.merchant_id, "charge.failed", charge_json(db, charge), request)
    body = pi_json(pi)
    emit(db, pi.merchant_id, "payment_intent.payment_failed", body, request)
    # A decline is a committed outcome (charge, events, idempotent response), not an error.
    return Result(402, {"error": {**error, "payment_intent": body}})


def confirm_pi(
    db: Session,
    pi: PaymentIntent,
    *,
    payment_method: PaymentMethod | None = None,
    return_url: str | None = None,
    request: Request | None = None,
) -> dict | Result:
    """Run the PI through the simulated network (caller holds the PI row lock)."""
    require_status(pi, "confirm")
    if payment_method is not None:
        pi.payment_method_id = payment_method.id
    if return_url:
        pi.return_url = return_url
    pi.last_payment_error = None
    pi.next_action = None
    if pi.payment_method_id is None:
        raise ApiError(
            400,
            "invalid_request_error",
            "You must provide a payment_method to confirm this PaymentIntent.",
            code="parameter_missing",
            param="payment_method",
        )
    pm = db.get(PaymentMethod, pi.payment_method_id)
    outcome = network.authorize(pm.sim_outcome, pi.amount)

    if isinstance(outcome, network.RequiresAuth):
        if not pi.return_url:
            raise ApiError(
                400,
                "invalid_request_error",
                "This card requires authentication; provide a return_url.",
                code="parameter_missing",
                param="return_url",
            )
        pi.status = "requires_action"
        # ponytail: the client_secret rides in the query string (as on Stripe). The M16/M18
        # page must send Referrer-Policy: no-referrer and never log query strings.
        pi.next_action = {
            "type": "redirect_to_url",
            "redirect_to_url": {
                "url": f"{config.public_base_url()}/checkout/3ds/{pi.id}"
                f"?client_secret={pi.client_secret}",
                "return_url": pi.return_url,
            },
        }
        db.flush()
        body = pi_json(pi)
        emit(db, pi.merchant_id, "payment_intent.requires_action", body, request)
        return body

    if isinstance(outcome, network.Approved):
        return approve(db, pi, pm, request)
    return decline(db, pi, pm, request, charge_code=outcome.code, decline_code=outcome.decline_code)
