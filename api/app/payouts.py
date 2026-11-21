"""Manual payouts of the available balance to a simulated bank (design §5.16)."""

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock, ledger
from app.errors import ApiError
from app.events import emit
from app.ids import new_id
from app.models import Payout

SETTLE_BATCH = 100


def payout_json(p: Payout) -> dict:
    return {
        "id": p.id,
        "object": "payout",
        "amount": p.amount,
        "currency": p.currency,
        "status": p.status,
        "arrival_date": int(p.arrival_date.timestamp()),
        "balance_transaction": p.balance_transaction_id,
        "created": int(p.created.timestamp()),
        "livemode": False,
    }


def available(db: Session, merchant_id: str, currency: str) -> int:
    for entry in ledger.balance(db, merchant_id)["available"]:
        if entry["currency"] == currency:
            return entry["amount"]
    return 0


def create_payout(
    db: Session,
    merchant_id: str,
    amount: int,
    currency: str = "usd",
    request: Request | None = None,
) -> Payout:
    # Lock first, then read: two concurrent payouts can't both pass the balance check.
    ledger.lock_available(db, merchant_id, currency)
    if amount > available(db, merchant_id, currency):
        raise ApiError(
            400,
            "invalid_request_error",
            "You have insufficient funds in your available balance for this payout.",
            code="balance_insufficient",
            param="amount",
        )
    now = clock.now()
    payout = Payout(
        id=new_id("po_"),
        merchant_id=merchant_id,
        amount=amount,
        currency=currency,
        status="pending",
        arrival_date=now + ledger.availability_delay(),
        created=now,
    )
    db.add(payout)
    db.flush()
    payout.balance_transaction_id = ledger.post_payout(
        db, merchant_id, payout.id, amount, currency
    ).id
    db.flush()
    emit(db, merchant_id, "payout.created", payout_json(payout), request)
    return payout


def pay_due_payouts(db: Session) -> int:
    """Mark arrived payouts paid; the caller (the worker's housekeeping) commits."""
    due = db.scalars(
        select(Payout)
        .where(Payout.status == "pending", Payout.arrival_date <= clock.now())
        .order_by(Payout.arrival_date)
        .limit(SETTLE_BATCH)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).all()
    paid = 0
    for payout in due:
        if ledger.settle_payout(db, payout):
            emit(db, payout.merchant_id, "payout.paid", payout_json(payout))
            paid += 1
    return paid
