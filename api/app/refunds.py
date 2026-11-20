"""Refunds (design §5.7): always lock the PI, then the charge, whichever was named."""

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import forms, ledger
from app.charges import charge_json
from app.errors import ApiError
from app.events import emit
from app.ids import new_id
from app.models import Charge, PaymentIntent, Refund
from app.tenancy import get_owned


def refund_json(refund: Refund) -> dict:
    return {
        "id": refund.id,
        "object": "refund",
        "amount": refund.amount,
        "charge": refund.charge_id,
        "payment_intent": refund.payment_intent_id,
        "status": refund.status,
        "reason": refund.reason,
        "balance_transaction": refund.balance_transaction_id,
        "currency": refund.currency,
        "created": int(refund.created.timestamp()),
        "livemode": False,
    }


def _invalid(message: str, code: str, param: str | None = None) -> ApiError:
    return ApiError(400, "invalid_request_error", message, code=code, param=param)


def create_refund(
    db: Session,
    merchant_id: str,
    *,
    charge_id: str | None,
    payment_intent_id: str | None,
    amount: int | None,
    reason: str | None,
    request: Request | None,
) -> dict:
    if not charge_id and not payment_intent_id:
        raise _invalid(
            "Provide a charge or a payment_intent to refund.", "parameter_missing", "charge"
        )
    if charge_id and payment_intent_id:
        raise _invalid(
            "Provide either charge or payment_intent, not both.",
            "parameter_invalid",
            "payment_intent",
        )

    # Lock order (design §5.2): PI first, then the charge — whichever id was given.
    if charge_id:
        named = get_owned(db, Charge, charge_id, merchant_id, param="charge")
        payment_intent_id = named.payment_intent_id
    pi = get_owned(
        db, PaymentIntent, payment_intent_id, merchant_id, param="payment_intent", for_update=True
    )
    target_id = charge_id or pi.latest_charge_id
    charge = None
    if target_id:
        charge = db.scalar(
            select(Charge)
            .where(Charge.id == target_id, Charge.payment_intent_id == pi.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    if charge is None and charge_id:
        raise _invalid(
            f"Charge {charge_id} doesn't belong to PaymentIntent {pi.id}.",
            "parameter_invalid",
            "charge",
        )
    if charge is None:
        raise _invalid(
            "This PaymentIntent has no captured charge to refund.", "charge_not_captured"
        )
    if not charge.captured or charge.amount_captured == 0:
        raise _invalid(
            "This charge hasn't been captured, so it can't be refunded.", "charge_not_captured"
        )
    remaining = charge.amount_captured - charge.amount_refunded
    if remaining == 0:
        raise _invalid(f"Charge {charge.id} has already been refunded.", "charge_already_refunded")
    amount = remaining if amount is None else amount
    forms.require_amount(amount, minimum=1, maximum=remaining)

    refund_id = new_id("re_")
    bt = ledger.post_refund(db, merchant_id, refund_id, amount, charge.currency)
    refund = Refund(
        id=refund_id,
        merchant_id=merchant_id,
        charge_id=charge.id,
        payment_intent_id=pi.id,
        amount=amount,
        currency=charge.currency,
        status="succeeded",
        reason=reason,
        balance_transaction_id=bt.id,
    )
    db.add(refund)
    charge.amount_refunded += amount
    db.flush()
    body = refund_json(refund)
    emit(db, merchant_id, "refund.created", body, request)
    emit(db, merchant_id, "charge.refunded", charge_json(db, charge), request)
    return body
