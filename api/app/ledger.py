"""Double-entry ledger (design §5.8). `post()` is the only writer of journal entries.

Sign convention: debits are positive, credits negative; every transaction sums to 0. Postgres
enforces that (and append-only) with triggers, so a bug here fails loudly instead of
silently mis-booking money.
"""

import os
from datetime import timedelta

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from app import clock
from app.ids import new_id
from app.models import (
    BalanceTransaction,
    JournalEntry,
    JournalTransaction,
    LedgerAccount,
    Payout,
)

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
    if amount < 1:
        raise ValueError("capture amount must be at least 1")
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


def release(db: Session, bt: BalanceTransaction) -> bool:
    """Pending → available: Dr merchant_pending net, Cr merchant_available net.

    The status change is the gate: only one caller can flip a pending charge to available
    (concurrent callers wait on the row and then match nothing), so a retried or racing
    release never moves money twice. Returns False if there was nothing to release."""
    db.flush()
    net = db.scalar(
        update(BalanceTransaction)
        .where(
            BalanceTransaction.id == bt.id,
            BalanceTransaction.type == "charge",
            BalanceTransaction.status == "pending",
        )
        .values(status="available")
        .returning(BalanceTransaction.net)
    )
    db.refresh(bt)
    if net is None:
        return False
    if net:
        post(
            db,
            "release",
            bt.id,
            bt.currency,
            [
                (account_id(db, bt.merchant_id, "merchant_pending", bt.currency), net),
                (account_id(db, bt.merchant_id, "merchant_available", bt.currency), -net),
            ],
        )
    return True


def post_refund(
    db: Session, merchant_id: str, source_id: str, amount: int, currency: str = "usd"
) -> BalanceTransaction:
    """Dr merchant_available R, Cr clearing R. Fees aren't returned; available may go < 0."""
    if amount < 1:
        raise ValueError("refund amount must be at least 1")
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


def lock_available(db: Session, merchant_id: str, currency: str) -> None:
    """Serialise payouts per merchant: lock the merchant_available account row before reading
    the balance (design §5.16; the last entry in the §5.2 lock order). Refunds don't take it,
    so a concurrent refund can still push available below zero afterwards (allowed)."""
    db.execute(
        select(LedgerAccount.id)
        .where(
            LedgerAccount.merchant_id == merchant_id,
            LedgerAccount.kind == "merchant_available",
            LedgerAccount.currency == currency,
        )
        .with_for_update()
    )


def post_payout(
    db: Session, merchant_id: str, payout_id: str, amount: int, currency: str = "usd"
) -> BalanceTransaction:
    """Money leaves available for the bank: Dr merchant_available A, Cr payouts_in_transit A."""
    if amount < 1:
        raise ValueError("payout amount must be at least 1")
    jtx_id = post(
        db,
        "payout",
        payout_id,
        currency,
        [
            (account_id(db, merchant_id, "merchant_available", currency), amount),
            (account_id(db, None, "payouts_in_transit", currency), -amount),
        ],
    )
    now = clock.now()
    bt = BalanceTransaction(
        id=new_id("txn_"),
        merchant_id=merchant_id,
        type="payout",
        amount=-amount,
        fee=0,
        net=-amount,
        currency=currency,
        status="available",
        available_on=now,
        source_id=payout_id,
        journal_transaction_id=jtx_id,
        created=now,
    )
    db.add(bt)
    db.flush()
    return bt


def settle_payout(db: Session, payout: Payout) -> bool:
    """The bank received it: Dr payouts_in_transit A, Cr card_network_clearing A (network
    settlement goes straight to the merchant's bank). The status flip is the gate, as in
    release(): a racing or repeated settle posts nothing."""
    db.flush()
    paid = db.scalar(
        update(Payout)
        .where(Payout.id == payout.id, Payout.status == "pending")
        .values(status="paid", paid_at=clock.now())
        .returning(Payout.amount)
    )
    db.refresh(payout)
    if paid is None:
        return False
    post(
        db,
        "payout_paid",
        payout.id,
        payout.currency,
        [
            (account_id(db, None, "payouts_in_transit", payout.currency), paid),
            (account_id(db, None, "card_network_clearing", payout.currency), -paid),
        ],
    )
    return True


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
