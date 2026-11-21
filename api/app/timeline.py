"""A payment's timeline: its events plus the journal postings behind them (dashboard detail,
and the public landing-page demo)."""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import (
    BalanceTransaction,
    Charge,
    Event,
    JournalEntry,
    JournalTransaction,
    LedgerAccount,
    PaymentIntent,
    Refund,
)


def _unix(value) -> int | None:
    return int(value.timestamp()) if value is not None else None


def for_payment(db: Session, merchant_id: str, pi: PaymentIntent) -> dict:
    charge_ids = list(db.scalars(select(Charge.id).where(Charge.payment_intent_id == pi.id)))
    refund_ids = list(db.scalars(select(Refund.id).where(Refund.payment_intent_id == pi.id)))
    object_ids = [pi.id, *charge_ids, *refund_ids]
    events = db.scalars(
        select(Event)
        .where(
            Event.merchant_id == merchant_id,
            or_(
                Event.data["object"]["id"].astext.in_(object_ids),
                Event.data["object"]["payment_intent"].astext == pi.id,
            ),
        )
        .order_by(Event.seq)
    ).all()
    # Captures and refunds post under the charge/refund id; releases under the capture's
    # balance transaction id.
    capture_bts = list(
        db.scalars(
            select(BalanceTransaction.id).where(
                BalanceTransaction.merchant_id == merchant_id,
                BalanceTransaction.source_id.in_(charge_ids),
            )
        )
    )
    sources = [*charge_ids, *refund_ids, *capture_bts]
    rows = db.execute(
        select(JournalTransaction, JournalEntry, LedgerAccount)
        .join(JournalEntry, JournalEntry.transaction_id == JournalTransaction.id)
        .join(LedgerAccount, LedgerAccount.id == JournalEntry.account_id)
        .where(
            JournalTransaction.source_id.in_(sources),
            or_(LedgerAccount.merchant_id == merchant_id, LedgerAccount.merchant_id.is_(None)),
        )
        .order_by(JournalTransaction.created, JournalTransaction.id, JournalEntry.id)
    ).all()
    postings: dict[str, dict] = {}
    for jtx, entry, account in rows:
        posting = postings.setdefault(
            jtx.id,
            {
                "id": jtx.id,
                "type": jtx.type,
                "source": jtx.source_id,
                "created": _unix(jtx.created),
                "entries": [],
            },
        )
        posting["entries"].append(
            {"account": account.kind, "amount": entry.amount, "currency": entry.currency}
        )
    return {
        "events": [
            {
                "id": e.id,
                "type": e.type,
                "created": _unix(e.created),
                # Events from one API request belong to one step of the payment's life.
                "request": (e.request or {}).get("id"),
            }
            for e in events
        ],
        "postings": list(postings.values()),
    }
