"""M15: housekeeping jobs (fund release, session purge) and run_housekeeping."""

import logging
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app import clock, housekeeping, ledger, seed, sessions
from app.ledger_check import ledger_check

T = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
FORM = {"Content-Type": "application/x-www-form-urlencoded"}
MANUAL = "amount=1000&currency=usd&capture_method=manual&payment_method=pm_card_visa&confirm=true"
KEYS = ("released", "expired_authorizations", "purged_idempotency_keys", "purged_sessions")


@pytest.fixture(autouse=True)
def _reconciles(db):
    yield
    db.flush()
    assert ledger_check(db, own_txn=False) == []


@pytest.fixture
def at(monkeypatch):
    monkeypatch.delenv("AVAILABILITY_DELAY_SECONDS", raising=False)

    def set_time(delta=timedelta(0)):
        when = T + delta
        monkeypatch.setattr(clock, "now", lambda: when)

    set_time()
    return set_time


@pytest.fixture
def merchant(db):
    return seed.seed(db, "hk-a@example.com")[0]


class _Begin:
    """A session factory whose .begin() yields the savepoint session and never commits."""

    def __init__(self, db):
        self.db = db

    @contextmanager
    def begin(self):
        yield self.db


def journal_count(db):
    return db.scalar(text("SELECT count(*) FROM journal_transactions"))


def session_count(db, merchant_id):
    return db.scalar(
        text("SELECT count(*) FROM dashboard_sessions WHERE merchant_id = :m"), {"m": merchant_id}
    )


def bal(db, merchant_id):
    b = ledger.balance(db, merchant_id)
    return b["pending"][0]["amount"], b["available"][0]["amount"]


# --- release_due_funds ---------------------------------------------------------------------


def test_release_due_capture(db, merchant, at):
    bt = ledger.post_capture(db, merchant.id, "ch_1", 1000)
    at(timedelta(days=2, seconds=1))
    assert housekeeping.release_due_funds(db) == 1
    db.refresh(bt)
    assert bt.status == "available"
    assert bal(db, merchant.id) == (0, 941)


def test_release_of_zero_net_marks_available_without_posting(db, merchant, at):
    bt = ledger.post_capture(db, merchant.id, "ch_x", 1)
    before = journal_count(db)
    at(timedelta(days=2, seconds=1))
    assert housekeeping.release_due_funds(db) == 1
    db.refresh(bt)
    assert bt.status == "available"
    assert journal_count(db) == before


def test_not_yet_due_capture_stays_pending(db, merchant, at):
    bt = ledger.post_capture(db, merchant.id, "ch_1", 1000)
    at(timedelta(days=1))
    assert housekeeping.release_due_funds(db) == 0
    db.refresh(bt)
    assert bt.status == "pending"
    assert bal(db, merchant.id) == (941, 0)


def test_refund_balance_transaction_is_untouched(db, merchant, at):
    bt = ledger.post_refund(db, merchant.id, "re_1", 400)
    before = journal_count(db)
    at(timedelta(days=3))
    assert housekeeping.release_due_funds(db) == 0
    db.refresh(bt)
    assert (bt.type, bt.status) == ("refund", "available")
    assert journal_count(db) == before


def test_second_release_run_returns_zero(db, merchant, at):
    ledger.post_capture(db, merchant.id, "ch_1", 1000)
    at(timedelta(days=2, seconds=1))
    assert housekeeping.release_due_funds(db) == 1
    before = journal_count(db)
    assert housekeeping.release_due_funds(db) == 0
    assert journal_count(db) == before
    assert bal(db, merchant.id) == (0, 941)


# --- purge_dashboard_sessions --------------------------------------------------------------


def test_purge_removes_expired_sessions(db, merchant, at):
    sessions.create_session(db, merchant.id)
    sessions.create_session(db, merchant.id)
    at(timedelta(days=8))
    assert housekeeping.purge_dashboard_sessions(db) == 2
    assert session_count(db, merchant.id) == 0


def test_purge_keeps_live_sessions(db, merchant, at):
    sessions.create_session(db, merchant.id)
    sessions.create_session(db, merchant.id)
    at(timedelta(days=1))
    assert housekeeping.purge_dashboard_sessions(db) == 0
    assert session_count(db, merchant.id) == 2


# --- run_housekeeping ----------------------------------------------------------------------


@pytest.fixture
def one_of_each(client, db, at):
    """At T: a capture, a manual authorization, an idempotency key and a session; then the
    clock moves past every expiry (7 days + 1s)."""
    m, sk, _ = seed.seed(db, "hk-run@example.com")
    h = {"Authorization": f"Bearer {sk}", **FORM}
    ledger.post_capture(db, m.id, "ch_due", 1000)
    r = client.post("/v1/payment_intents", content=MANUAL, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "requires_capture"
    r = client.post("/v1/customers", content="", headers={**h, "Idempotency-Key": "k1"})
    assert r.status_code == 200, r.text
    sessions.create_session(db, m.id)
    at(timedelta(days=7, seconds=1))
    return m


def test_run_housekeeping_runs_every_job(db, one_of_each):
    result = housekeeping.run_housekeeping(_Begin(db))
    assert result == {
        **dict.fromkeys(KEYS, 1),
        "expired_checkout_sessions": 0,
        "paid_payouts": 0,  # M23 (user-approved milestone): one more housekeeping job
    }


def test_run_housekeeping_isolates_a_failing_job(db, one_of_each, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(housekeeping, "release_due_funds", boom)
    result = housekeeping.run_housekeeping(_Begin(db))
    assert result == {
        "released": None,
        "expired_authorizations": 1,
        "purged_idempotency_keys": 1,
        "purged_sessions": 1,
        "expired_checkout_sessions": 0,
        "paid_payouts": 0,
    }
    assert any(r.levelno >= logging.ERROR for r in caplog.records)
