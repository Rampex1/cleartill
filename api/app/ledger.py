"""Double-entry ledger (design §5.8). `post()` is the only writer of journal entries.

Sign convention: debits are positive, credits negative; every transaction sums to 0. Postgres
enforces that (and append-only) with triggers, so a bug here fails loudly instead of
silently mis-booking money.
"""

import os
from datetime import timedelta

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app import clock
from app.ids import new_id
from app.models import BalanceTransaction, JournalEntry, JournalTransaction, LedgerAccount

MERCHANT_KINDS = ("merchant_pending", "merchant_available")


def fee(amount: int) -> int:
    """2.9% rounded half up, plus 30¢, capped at the amount (integer cents)."""
    return min((amount * 29 + 500) // 1000 + 30, amount)


def availability_delay() -> timedelta:
    return timedelta(seconds=int(os.environ.get("AVAILABILITY_DELAY_SECONDS") or 172800))


def account_id(db: Session, merchant_id: str | None, kind: str, currency: str) -> str:
    owner = (
        LedgerAccount.merchant_id.is_(None)
        if merchant_id is None
        else LedgerAccount.merchant_id == merchant_id
    )
    found = db.scalar(
        select(LedgerAccount.id).where(
            owner, LedgerAccount.kind == kind, LedgerAccount.currency == currency
        )
    )
    if found is None:
        raise LookupError(f"no {kind} {currency} ledger account for {merchant_id or 'platform'}")
    return found


def create_merchant_accounts(db: Session, merchant_id: str, currency: str = "usd") -> None:
    for kind in MERCHANT_KINDS:
        db.add(
            LedgerAccount(id=new_id("la_"), merchant_id=merchant_id, kind=kind, currency=currency)
        )
    db.flush()


def post(db: Session, type: str, source_id: str, currency: str, legs: list[tuple[str, int]]) -> str:
    """Write one balanced journal transaction; returns its id. Zero legs are dropped."""
    legs = [(account, amount) for account, amount in legs if amount != 0]
    if len(legs) < 2:
        raise ValueError("a journal transaction needs at least two non-zero legs")
    jtx_id = new_id("jtx_")
    db.execute(
        insert(JournalTransaction).values(
            id=jtx_id, type=type, source_id=source_id, created=clock.now()
        )
    )
    # ONE multi-row INSERT: the balance trigger checks per statement. `db.execute(insert(t),
    # rows)` or `add_all` would insert row by row (psycopg executemany) and trip it.
    db.execute(
        insert(JournalEntry).values(
            [
                {
                    "transaction_id": jtx_id,
                    "account_id": account,
                    "amount": amount,
                    "currency": currency,
                }
                for account, amount in legs
            ]
        )
    )
    return jtx_id


def post_capture(
    db: Session, merchant_id: str, source_id: str, amount: int, currency: str = "usd"
) -> BalanceTransaction:
    """Captured funds: Dr clearing A, Cr merchant_pending net, Cr platform_fees fee."""
    charge_fee = fee(amount)
    net = amount - charge_fee
    jtx_id = post(
        db,
        "charge",
        source_id,
        currency,
        [
            (account_id(db, None, "card_network_clearing", currency), amount),
            (account_id(db, merchant_id, "merchant_pending", currency), -net),
            (account_id(db, None, "platform_fees", currency), -charge_fee),
        ],
    )
    now = clock.now()
    bt = BalanceTransaction(
        id=new_id("txn_"),
        merchant_id=merchant_id,
        type="charge",
        amount=amount,
        fee=charge_fee,
        net=net,
        currency=currency,
        status="pending",
        available_on=now + availability_delay(),
        source_id=source_id,
        journal_transaction_id=jtx_id,
        created=now,
    )
    db.add(bt)
    db.flush()
    return bt


def release(db: Session, bt: BalanceTransaction) -> None:
    """Pending → available: Dr merchant_pending net, Cr merchant_available net."""
    if bt.net:
        post(
            db,
            "release",
            bt.id,
            bt.currency,
            [
                (account_id(db, bt.merchant_id, "merchant_pending", bt.currency), bt.net),
                (account_id(db, bt.merchant_id, "merchant_available", bt.currency), -bt.net),
            ],
        )
    bt.status = "available"
    db.flush()


def post_refund(
    db: Session, merchant_id: str, source_id: str, amount: int, currency: str = "usd"
) -> BalanceTransaction:
    """Dr merchant_available R, Cr clearing R. Fees aren't returned; available may go < 0."""
    jtx_id = post(
        db,
        "refund",
        source_id,
        currency,
        [
            (account_id(db, merchant_id, "merchant_available", currency), amount),
            (account_id(db, None, "card_network_clearing", currency), -amount),
        ],
    )
    now = clock.now()
    bt = BalanceTransaction(
        id=new_id("txn_"),
        merchant_id=merchant_id,
        type="refund",
        amount=-amount,
        fee=0,
        net=-amount,
        currency=currency,
        status="available",
        available_on=now,
        source_id=source_id,
        journal_transaction_id=jtx_id,
        created=now,
    )
    db.add(bt)
    db.flush()
    return bt


def balance(db: Session, merchant_id: str) -> dict:
    rows = db.execute(
        select(
            LedgerAccount.kind,
            LedgerAccount.currency,
            func.coalesce(func.sum(JournalEntry.amount), 0),
        )
        .outerjoin(JournalEntry, JournalEntry.account_id == LedgerAccount.id)
        .where(LedgerAccount.merchant_id == merchant_id)
        .group_by(LedgerAccount.kind, LedgerAccount.currency)
    ).all()
    totals = {(kind, currency): -int(total) for kind, currency, total in rows}  # credit-normal
    currencies = sorted({currency for _, currency in totals})
    return {
        "available": [
            {"amount": totals.get(("merchant_available", c), 0), "currency": c} for c in currencies
        ],
        "pending": [
            {"amount": totals.get(("merchant_pending", c), 0), "currency": c} for c in currencies
        ],
    }
