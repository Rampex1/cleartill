"""Hosted checkout sessions (design §5.10). Lock order (§5.2): session, then PI, then charge."""

from datetime import timedelta

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock, config
from app.events import emit
from app.models import CheckoutSession, PaymentIntent
from app.payment_intents import pi_json

SESSION_LIFETIME = timedelta(hours=24)
EXPIRY_BATCH = 100
CANCELABLE = {"requires_payment_method", "requires_confirmation", "requires_action"}


def is_open(cs: CheckoutSession) -> bool:
    return cs.status == "open" and cs.expires_at > clock.now()


def session_json(cs: CheckoutSession) -> dict:
    return {
        "id": cs.id,
        "object": "checkout.session",
        "mode": "payment",
        "amount_total": cs.amount_total,
        "currency": cs.currency,
        "status": cs.status,
        "payment_status": cs.payment_status,
        "payment_intent": cs.payment_intent_id,
        "success_url": cs.success_url,
        "cancel_url": cs.cancel_url,
        "url": f"{config.public_base_url()}/checkout/{cs.id}" if cs.status == "open" else None,
        "customer": cs.customer_id,
        "client_reference_id": cs.client_reference_id,
        "metadata": cs.metadata_,
        "expires_at": int(cs.expires_at.timestamp()),
        "created": int(cs.created.timestamp()),
        "livemode": False,
    }


def public_json(db: Session, cs: CheckoutSession) -> dict:
    """What the customer's browser sees: no PI, no client_secret, no merchant data."""
    pi = db.get(PaymentIntent, cs.payment_intent_id)
    error = pi.last_payment_error if pi is not None else None
    status = "expired" if cs.status == "open" and not is_open(cs) else cs.status
    return {
        "id": cs.id,
        "status": status,
        "amount_total": cs.amount_total,
        "currency": cs.currency,
        "line_items": cs.line_items,
        "success_url": cs.success_url,
        "cancel_url": cs.cancel_url,
        "last_payment_error": {"message": error["message"]} if error else None,
    }


def lock_session(db: Session, cs_id: str) -> CheckoutSession | None:
    return db.scalar(
        select(CheckoutSession)
        .where(CheckoutSession.id == cs_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def success_redirect(cs: CheckoutSession) -> str:
    return cs.success_url.replace("{CHECKOUT_SESSION_ID}", cs.id)


def complete_if_paid(db: Session, pi: PaymentIntent, request: Request | None) -> None:
    """Same transaction as the PI's success; the caller holds the session lock."""
    if pi.checkout_session_id is None or pi.status != "succeeded":
        return
    cs = db.get(CheckoutSession, pi.checkout_session_id)
    cs.status = "complete"
    cs.payment_status = "paid"
    db.flush()
    emit(db, cs.merchant_id, "checkout.session.completed", session_json(cs), request)


def expire_sessions(db: Session) -> int:
    """Expire open sessions past expires_at and cancel their PIs; the caller commits."""
    due = db.scalars(
        select(CheckoutSession)
        .where(CheckoutSession.status == "open", CheckoutSession.expires_at <= clock.now())
        .order_by(CheckoutSession.expires_at)
        .limit(EXPIRY_BATCH)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).all()
    expired = 0
    for cs in due:
        if cs.status != "open":
            continue
        pi = db.scalar(
            select(PaymentIntent)
            .where(PaymentIntent.id == cs.payment_intent_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        cs.status = "expired"
        db.flush()
        emit(db, cs.merchant_id, "checkout.session.expired", session_json(cs))
        if pi.status in CANCELABLE:
            pi.status = "canceled"
            pi.cancellation_reason = "abandoned"
            pi.next_action = None
            db.flush()
            emit(db, pi.merchant_id, "payment_intent.canceled", pi_json(pi))
        expired += 1
    return expired
