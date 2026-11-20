"""Regression tests for the M15 review: delivery can't starve housekeeping, a failed startup
check doesn't kill the worker, bad rows don't block releases, job writes roll back alone,
concurrent releasers post once."""

import logging
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app import clock, housekeeping, ledger, seed, worker
from app.db import SessionLocal
from app.ids import new_id
from app.ledger_check import ledger_check

T = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def at(monkeypatch):
    monkeypatch.delenv("AVAILABILITY_DELAY_SECONDS", raising=False)

    def set_time(delta=timedelta(0)):
        when = T + delta
        monkeypatch.setattr(clock, "now", lambda: when)

    set_time()
    return set_time


@pytest.fixture
def fresh_tick(monkeypatch):
    monkeypatch.setattr(worker, "_last_housekeeping", None)


def test_delivery_error_does_not_stop_housekeeping(monkeypatch, fresh_tick):
    calls = []
    monkeypatch.setattr(worker.housekeeping, "run_housekeeping", lambda *a: calls.append(1) or {})

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(worker.delivery, "deliver_one", boom)
    assert worker.tick(1000.0) is True
    assert calls == [1]


def test_housekeeping_error_does_not_stop_delivery(monkeypatch, fresh_tick):
    sent = []

    def boom(*a):
        raise RuntimeError("boom")

    monkeypatch.setattr(worker.housekeeping, "run_housekeeping", boom)
    monkeypatch.setattr(worker.delivery, "deliver_one", lambda: sent.append(1) and False)
    assert worker.tick(1000.0) is True
    assert sent == [1]


def test_endless_backlog_is_bounded_by_the_delivery_budget(monkeypatch):
    monkeypatch.setattr(worker.delivery, "deliver_one", lambda: True)
    assert worker.run_once(budget_seconds=0.05) > 0  # returns instead of looping forever


def test_startup_check_that_cannot_run_is_critical_not_fatal(engine, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("relation does not exist")

    monkeypatch.setattr(worker, "ledger_check", boom)
    with caplog.at_level(logging.CRITICAL, logger="cleartill.worker"):
        assert worker.check_ledger_on_start(engine) == ["ledger check could not run"]
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)


def test_a_bad_row_is_skipped_and_the_rest_released(db, at, monkeypatch):
    m = seed.seed(db, "hk-r1@example.com")[0]
    first = ledger.post_capture(db, m.id, "ch_a", 1000)
    second = ledger.post_capture(db, m.id, "ch_b", 1000)
    at(timedelta(days=3))
    real = ledger.release

    def flaky(db_, bt):
        if bt.id == first.id:
            raise RuntimeError("bad row")
        return real(db_, bt)

    monkeypatch.setattr(ledger, "release", flaky)
    assert housekeeping.release_due_funds(db) == 1
    db.refresh(first)
    db.refresh(second)
    assert (first.status, second.status) == ("pending", "available")
    assert ledger_check(db, own_txn=False) == []


def test_a_failing_job_rolls_back_only_its_own_writes(db, at, monkeypatch):
    m = seed.seed(db, "hk-r2@example.com")[0]
    bt = ledger.post_capture(db, m.id, "ch_c", 1000)
    at(timedelta(days=3))

    class Savepoints:
        @contextmanager
        def begin(self):
            with db.begin_nested():
                yield db

    def release_then_fail(db_):
        housekeeping.release_due_funds(db_)
        raise RuntimeError("after writing")

    monkeypatch.setattr(housekeeping, "release_due_funds", release_then_fail)
    results = housekeeping.run_housekeeping(Savepoints())
    assert results["released"] is None
    assert all(results[k] is not None for k in results if k != "released")
    db.expire_all()
    assert db.get(type(bt), bt.id).status == "pending"
    assert ledger_check(db, own_txn=False) == []


def test_two_workers_release_each_row_once(engine, monkeypatch):
    # Committed data: the ledger rows stay behind (consistent), as CLAUDE.md allows.
    monkeypatch.delenv("AVAILABILITY_DELAY_SECONDS", raising=False)
    with SessionLocal.begin() as s:
        m = seed.seed(s, f"{new_id('hkc_')}@example.com")[0]
        merchant_id = m.id
        for _ in range(20):
            ledger.post_capture(s, merchant_id, new_id("ch_"), 1000)
    later = clock.now() + timedelta(days=3)
    monkeypatch.setattr(clock, "now", lambda: later)
    barrier = threading.Barrier(2)

    def work():
        barrier.wait()
        with SessionLocal.begin() as s:
            housekeeping.release_due_funds(s)

    threads = [threading.Thread(target=work) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    with SessionLocal() as s:
        b = ledger.balance(s, merchant_id)
        assert b["pending"][0]["amount"] == 0
        assert b["available"][0]["amount"] == 20 * 941  # a double release would exceed this
    assert ledger_check(engine) == []
