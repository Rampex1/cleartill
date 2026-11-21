"""M24 (Render): public URL from Render's environment, and the worker running in-process."""

import threading

from fastapi.testclient import TestClient

from app import config, worker
from app.main import app


def _with_env(monkeypatch, **env):
    for key in ("PUBLIC_BASE_URL", "RENDER_EXTERNAL_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config.public_base_url.cache_clear()


def test_public_base_url_falls_back_to_render_external_url(monkeypatch):
    try:
        _with_env(monkeypatch, RENDER_EXTERNAL_URL="https://cleartill-x1.onrender.com/")
        assert config.public_base_url() == "https://cleartill-x1.onrender.com"
    finally:
        monkeypatch.undo()
        config.public_base_url.cache_clear()


def test_explicit_public_base_url_wins(monkeypatch):
    try:
        _with_env(
            monkeypatch,
            PUBLIC_BASE_URL="https://pay.example.com",
            RENDER_EXTERNAL_URL="https://cleartill.onrender.com",
        )
        assert config.public_base_url() == "https://pay.example.com"
    finally:
        monkeypatch.undo()
        config.public_base_url.cache_clear()


def test_in_process_worker_ticks_until_stopped(monkeypatch):
    ticked = threading.Event()
    monkeypatch.setattr(worker, "check_ledger_on_start", lambda bind: [])
    monkeypatch.setattr(worker, "tick", lambda now: ticked.set())
    monkeypatch.setattr(worker, "IDLE_SLEEP_SECONDS", 0.01)
    thread = worker.start_in_process()
    try:
        assert ticked.wait(5)
    finally:
        worker.stop()
        thread.join(5)
        worker._stop.clear()  # module state: later tests drive the loop directly
    assert not thread.is_alive()


def test_app_lifespan_starts_and_stops_the_worker_only_when_asked(monkeypatch):
    started, stopped = [], []

    class FakeThread:
        def join(self, timeout=None):
            stopped.append(timeout)

    monkeypatch.setattr(worker, "start_in_process", lambda: started.append(1) or FakeThread())
    monkeypatch.setattr(worker, "stop", lambda: None)
    with TestClient(app):
        pass
    assert started == []  # off by default (local dev runs the compose worker service)
    monkeypatch.setenv("RUN_WORKER_IN_PROCESS", "1")
    with TestClient(app):
        assert started == [1]
    assert stopped == [20]
