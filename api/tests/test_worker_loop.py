"""M15: worker start-up ledger check and the housekeeping cadence in tick()."""

import logging

import pytest

from app import worker


def test_check_ledger_on_start_logs_critical_on_problems(engine, monkeypatch, caplog):
    monkeypatch.setattr(worker, "ledger_check", lambda *a, **k: ["boom"])
    with caplog.at_level(logging.CRITICAL, logger="cleartill.worker"):
        assert worker.check_ledger_on_start(engine) == ["boom"]
    assert any(
        r.levelno == logging.CRITICAL and r.name == "cleartill.worker" for r in caplog.records
    )


def test_check_ledger_on_start_is_quiet_when_clean(engine, monkeypatch, caplog):
    monkeypatch.setattr(worker, "ledger_check", lambda *a, **k: [])
    with caplog.at_level(logging.DEBUG):
        assert worker.check_ledger_on_start(engine) == []
    assert not [r for r in caplog.records if r.levelno >= logging.CRITICAL]


@pytest.fixture
def hk_calls(monkeypatch):
    calls = []

    def fake_run(*a, **k):
        calls.append(a)
        return {}

    monkeypatch.setattr(worker, "_last_housekeeping", None)
    monkeypatch.setattr(worker.housekeeping, "run_housekeeping", fake_run)
    monkeypatch.setattr(worker.delivery, "deliver_one", lambda *a, **k: False)
    return calls


def test_housekeeping_interval_is_60s():
    assert worker.HOUSEKEEPING_INTERVAL == 60


def test_tick_runs_housekeeping_on_first_tick_and_every_interval(hk_calls):
    assert worker.tick(1000.0) is True
    assert worker.tick(1030.0) is False
    assert worker.tick(1061.0) is True
    assert len(hk_calls) == 2
