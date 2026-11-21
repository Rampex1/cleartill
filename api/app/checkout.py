"""Hosted checkout sessions (design §5.10). Lock order (§5.2): session, then PI, then charge."""

import logging
from datetime import timedelta

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import clock, config
from app.errors import ApiError
from app.events import emit
from app.ids import new_id
from app.models import Charge, CheckoutSession, PaymentIntent
from app.payment_intents import create_pi, pi_json

log = logging.getLogger("cleartill.checkout")

SESSION_LIFETIME = timedelta(hours=24)
EXPIRY_BATCH = 100
# Bounds what a looped decline on one session URL can write (PMs, charges, events, webhooks).
MAX_FAILED_ATTEMPTS = 20
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
        "success_url": success_redirect(cs),
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


def require_open(cs: CheckoutSession) -> None:
    if not is_open(cs):
        raise ApiError(
            400,
            "invalid_request_error",
            "This checkout session is no longer accepting payments.",
            code="checkout_session_unexpected_state",
        )


def require_attempts_left(db: Session, cs: CheckoutSession) -> None:
    failed = db.scalar(
        select(func.count())
        .select_from(Charge)
        .where(Charge.payment_intent_id == cs.payment_intent_id, Charge.status == "failed")
    )
    if failed >= MAX_FAILED_ATTEMPTS:
        raise ApiError(
            400,
            "invalid_request_error",
            "Too many failed payment attempts for this checkout session.",
            code="checkout_session_unexpected_state",
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
        try:
            with db.begin_nested():  # one bad row is logged and skipped, not the whole batch
                expired += _expire(db, cs)
        except Exception:
            log.exception("could not expire checkout session %s", cs.id)
    return expired


def _expire(db: Session, cs: CheckoutSession) -> int:
    pi = db.scalar(
        select(PaymentIntent)
        .where(PaymentIntent.id == cs.payment_intent_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if pi.status not in CANCELABLE:  # completion is atomic with success, so this is a bug
        log.error("open session %s has PI in %s; not expiring", cs.id, pi.status)
        return 0
    cs.status = "expired"
    db.flush()
    emit(db, cs.merchant_id, "checkout.session.expired", session_json(cs))
    pi.status = "canceled"
    pi.cancellation_reason = "abandoned"
    pi.next_action = None
    db.flush()
    emit(db, pi.merchant_id, "payment_intent.canceled", pi_json(pi))
    return 1


def create_session(
    db: Session,
    merchant_id: str,
    items: list[dict],
    *,
    success_url: str,
    cancel_url: str | None = None,
    customer_id: str | None = None,
    client_reference_id: str | None = None,
    metadata: dict | None = None,
    request: Request | None = None,
) -> CheckoutSession:
    """A session plus its eager PI (emits payment_intent.created). `items` are validated
    line items: [{name, quantity, unit_amount, amount_total}]."""
    cs = CheckoutSession(
        id=new_id("cs_"),
        merchant_id=merchant_id,
        amount_total=sum(i["amount_total"] for i in items),
        currency="usd",
        line_items=items,
        status="open",
        payment_status="unpaid",
        success_url=success_url,
        cancel_url=cancel_url,
        customer_id=customer_id,
        client_reference_id=client_reference_id,
        metadata_=metadata or {},
        expires_at=clock.now() + SESSION_LIFETIME,
    )
    db.add(cs)
    db.flush()
    pi = create_pi(
        db,
        merchant_id,
        amount=cs.amount_total,
        currency="usd",
        capture_method="automatic",
        customer_id=customer_id,
        return_url=session_json(cs)["url"],
        checkout_session_id=cs.id,
        request=request,
    )
    cs.payment_intent_id = pi.id
    db.flush()
    return cs
