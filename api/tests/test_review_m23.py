# ruff: noqa: F811  (pytest fixtures shared from test_payouts)
"""Regression tests for the M23 review: a data-safe downgrade, each new invariant on its own,
negative balances blocking payouts, the upper amount bound, isolated settlement."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app import ledger, payouts
from app.ledger_check import ledger_check
from tests.test_payouts import (  # noqa: F401
    _reconciles,
    assert_400,
    at,
    funded,
    keys,
    make_payout,
    mid,
    payout,
    skh,
)

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0016_payouts.py"


def _migration():
    spec = importlib.util.spec_from_file_location("m0016", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_downgrade_keeps_a_payouts_account_that_has_entries(db, mid, funded):
    """Runs inside the test's transaction (Postgres DDL is transactional): nothing persists."""
    make_payout(db, mid, 2500)
    db.flush()
    with Operations.context(MigrationContext.configure(db.connection())):
        _migration().downgrade()
    kinds = db.scalars(
        text("SELECT kind FROM ledger_accounts WHERE id = 'la_platform_payouts_usd'")
    ).all()
    assert kinds == ["payouts_in_transit"]
    assert db.scalar(text("SELECT to_regclass('payouts')")) is None
    db.rollback()  # undo the DDL before the autouse ledger check reads the payouts table


def _problems_with(db, label):
    return [p for p in ledger_check(db, own_txn=False) if p.startswith(label)]


def test_in_transit_check_on_its_own(db, mid, funded):
    p = make_payout(db, mid, 2500)
    db.flush()
    # Mark paid with matching postings removed from view: shift the pending total only.
    db.execute(text("UPDATE payouts SET status = 'paid' WHERE id = :p"), {"p": p.id})
    assert _problems_with(db, "payouts in transit don't match pending payouts")
    db.execute(text("UPDATE payouts SET status = 'pending' WHERE id = :p"), {"p": p.id})


def test_clearing_check_on_its_own(db, mid, funded):
    p = make_payout(db, mid, 2500)
    db.flush()
    db.execute(text("UPDATE payouts SET status = 'paid' WHERE id = :p"), {"p": p.id})
    assert _problems_with(db, "card clearing doesn't match")
    db.execute(text("UPDATE payouts SET status = 'pending' WHERE id = :p"), {"p": p.id})


def test_balance_transaction_match_check_on_its_own(db, mid, funded):
    p = make_payout(db, mid, 2500)
    db.flush()
    db.execute(text("UPDATE payouts SET balance_transaction_id = NULL WHERE id = :p"), {"p": p.id})
    assert _problems_with(db, "payout doesn't match its balance transaction")
    db.execute(
        text("UPDATE payouts SET balance_transaction_id = :b WHERE id = :p"),
        {"p": p.id, "b": p.balance_transaction_id},
    )


def test_negative_available_balance_blocks_payouts(client, db, mid, skh, funded):
    assert payout(client, skh, amount=str(funded), currency="usd").status_code == 200
    ledger.post_refund(db, mid, "re_after_payout", 300)  # available is now -300
    assert_400(payout(client, skh, amount="1", currency="usd"), "balance_insufficient", "amount")


def test_amount_above_the_8_digit_cap_is_too_large(client, skh, funded):
    assert_400(payout(client, skh, amount="100000000", currency="usd"), "amount_too_large")


def test_one_bad_payout_does_not_block_settlement(db, mid, funded, at, monkeypatch):
    from datetime import timedelta

    first = make_payout(db, mid, 1000)
    second = make_payout(db, mid, 1000)
    at(timedelta(days=3))
    real = ledger.settle_payout

    def flaky(db_, p):
        if p.id == first.id:
            raise RuntimeError("bad payout")
        return real(db_, p)

    monkeypatch.setattr(ledger, "settle_payout", flaky)
    assert payouts.pay_due_payouts(db) == 1
    db.refresh(first)
    db.refresh(second)
    assert (first.status, second.status) == ("pending", "paid")
