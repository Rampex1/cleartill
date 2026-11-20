"""Regression tests for the M6 ledger review."""

import threading

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app import ledger, seed
from app.db import SessionLocal
from app.ids import new_id
from app.ledger_check import ledger_check
from app.models import BalanceTransaction, JournalTransaction


@pytest.fixture
def merchant(db):
    m, _, _ = seed.seed(db, "r6@example.com")
    return m


def jtx_count(db):
    return db.scalar(select(text("count(*)")).select_from(JournalTransaction))


def test_release_twice_moves_money_once(db, merchant):
    bt = ledger.post_capture(db, merchant.id, "ch_1", 1000)
    assert ledger.release(db, bt) is True
    before = jtx_count(db)
    assert ledger.release(db, bt) is False
    assert jtx_count(db) == before
    assert ledger.balance(db, merchant.id)["available"][0]["amount"] == 941
    assert ledger_check(db, own_txn=False) == []


def test_release_of_a_refund_does_nothing(db, merchant):
    refund = ledger.post_refund(db, merchant.id, "re_1", 400)
    before = jtx_count(db)
    assert ledger.release(db, refund) is False
    assert jtx_count(db) == before
    assert ledger_check(db, own_txn=False) == []


@pytest.mark.parametrize("fn", ["post_capture", "post_refund"])
@pytest.mark.parametrize("amount", [0, -100])
def test_non_positive_amounts_are_rejected(db, merchant, fn, amount):
    with pytest.raises(ValueError):
        getattr(ledger, fn)(db, merchant.id, "x_1", amount)


def test_money_tests_reconcile(db, merchant):
    ledger.release(db, ledger.post_capture(db, merchant.id, "ch_1", 1000))
    ledger.post_capture(db, merchant.id, "ch_2", 31)
    ledger.post_refund(db, merchant.id, "re_1", 400)
    assert ledger_check(db, own_txn=False) == []


def test_fee_mismatch_is_reported(db, merchant):
    db.execute(
        text(
            "INSERT INTO balance_transactions (id, merchant_id, type, amount, fee, net, "
            "currency, status, available_on, source_id, created) VALUES "
            "(:id, :m, 'charge', 100, 50, 50, 'usd', 'available', now(), 'x', now())"
        ),
        {"id": new_id("txn_"), "m": merchant.id},
    )
    problems = ledger_check(db, own_txn=False)
    assert any("platform fees" in p for p in problems)


def test_amount_fee_net_mismatch_is_reported(db, merchant):
    db.execute(
        text(
            "INSERT INTO balance_transactions (id, merchant_id, type, amount, fee, net, "
            "currency, status, available_on, source_id, created) VALUES "
            "(:id, :m, 'charge', 100, 10, 0, 'usd', 'pending', now(), 'x', now())"
        ),
        {"id": new_id("txn_"), "m": merchant.id},
    )
    assert any("amount - fee" in p for p in ledger_check(db, own_txn=False))


def test_truncate_of_the_journal_is_rejected(db):
    for table in ("journal_entries", "journal_transactions"):
        with pytest.raises(DBAPIError):
            with db.begin_nested():
                db.execute(text(f"TRUNCATE {table} CASCADE"))


def test_merchant_with_money_history_cannot_be_deleted(db, merchant):
    ledger.post_capture(db, merchant.id, "ch_1", 1)  # net 0: touches only platform accounts
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(text("DELETE FROM merchants WHERE id = :m"), {"m": merchant.id})
    assert db.scalar(select(text("count(*)")).select_from(BalanceTransaction)) >= 1


def test_entry_currency_must_match_its_account(db, merchant):
    usd_pending = ledger.account_id(db, merchant.id, "merchant_pending", "usd")
    usd_available = ledger.account_id(db, merchant.id, "merchant_available", "usd")
    jtx = new_id("jtx_")
    db.execute(
        text(
            "INSERT INTO journal_transactions (id, type, source_id, created) "
            "VALUES (:j, 't', 's', now())"
        ),
        {"j": jtx},
    )
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(
                text(
                    "INSERT INTO journal_entries (transaction_id, account_id, amount, currency) "
                    "VALUES (:j, :a, 100, 'eur'), (:j, :b, -100, 'eur')"
                ),
                {"j": jtx, "a": usd_pending, "b": usd_available},
            )


def test_concurrent_release_moves_money_once(engine):
    """Committed data: two sessions release the same balance transaction at once."""
    with SessionLocal.begin() as s:
        m, _, _ = seed.seed(s, f"{new_id('r6c_')}@example.com")
        bt_id = ledger.post_capture(s, m.id, new_id("ch_"), 1000).id
        mid = m.id
    barrier = threading.Barrier(2)
    results = []

    def run():
        with SessionLocal.begin() as s:
            bt = s.get(BalanceTransaction, bt_id)
            barrier.wait()
            results.append(ledger.release(s, bt))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(results) == [False, True]
    with SessionLocal() as s:
        assert ledger.balance(s, mid)["available"][0]["amount"] == 941
    # Committed money history stays (the journal is append-only); it is consistent.
    assert ledger_check(engine) == []
