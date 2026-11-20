# ruff: noqa: F811  (pytest fixtures shared from test_webhook_worker / test_dashboard_webhooks)
"""Regression tests for the M20 review: no connection held during a resend, resend limits,
fenced-out finishes, disabled endpoints, deleted endpoints' logs, worker/resend fencing."""

import threading

from sqlalchemy import text
from test_dashboard_webhooks import (  # noqa: F401
    _reset_ratelimit,
    dash,
    err,
    list_deliveries,
    make_dash,
    resend,
    setup,
    sk_post,
)
from test_webhook_worker import (  # noqa: F401
    claim_ours,
    insecure,
    receiver_factory,
    row,
    sql,
)

from app import delivery
from app.db import SessionLocal, engine
from app.routers import dashboard


def test_no_connection_is_idle_in_a_transaction_during_the_send(
    dash, insecure, receiver_factory, monkeypatch
):
    receiver = receiver_factory(200)
    _, wd = setup(dash, receiver.url)
    seen = []
    real = delivery.send

    def spy(*args):
        with engine.connect() as conn:
            seen.append(
                conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                        " AND state = 'idle in transaction'"
                    )
                )
            )
        return real(*args)

    monkeypatch.setattr(delivery, "send", spy)
    assert resend(dash, wd["id"]).status_code == 200
    assert seen == [0]


def test_resends_are_rate_limited_per_merchant(dash, insecure, receiver_factory, monkeypatch):
    receiver = receiver_factory(200)
    _, wd = setup(dash, receiver.url)
    monkeypatch.setattr(dashboard, "RESENDS_PER_MINUTE", 2)
    assert [resend(dash, wd["id"]).status_code for _ in range(3)] == [200, 200, 429]


def test_resend_slots_full_is_429_and_sends_nothing(dash, insecure, receiver_factory, monkeypatch):
    receiver = receiver_factory(200)
    _, wd = setup(dash, receiver.url)
    monkeypatch.setattr(dashboard, "RESEND_SLOTS", threading.BoundedSemaphore(1))
    dashboard.RESEND_SLOTS.acquire()
    r = resend(dash, wd["id"])
    assert r.status_code == 429 and err(r)["code"] == "rate_limit"
    assert receiver.requests == []


def test_fenced_out_finish_is_409_not_an_old_attempt(dash, insecure, receiver_factory, monkeypatch):
    receiver = receiver_factory(200)
    _, wd = setup(dash, receiver.url)
    monkeypatch.setattr(delivery, "finish", lambda *a, **k: False)
    r = resend(dash, wd["id"])
    assert r.status_code == 409 and err(r)["code"] == "delivery_in_progress"


def test_resend_to_a_disabled_endpoint_is_400(dash, insecure, receiver_factory):
    receiver = receiver_factory(200)
    ep, wd = setup(dash, receiver.url)
    sk_post(dash, f"/v1/webhook_endpoints/{ep['id']}", "disabled=true")
    r = resend(dash, wd["id"])
    assert r.status_code == 400 and err(r)["code"] == "endpoint_unavailable"
    assert receiver.requests == []


def test_a_deleted_endpoints_log_stays_visible(dash, insecure, receiver_factory):
    receiver = receiver_factory(200)
    ep, wd = setup(dash, receiver.url)
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        assert c.delete(f"/v1/webhook_endpoints/{ep['id']}", headers=dash.sk).status_code == 200
    assert [d["id"] for d in list_deliveries(dash, ep["id"])] == [wd["id"]]


def test_a_manual_claim_blocks_the_worker(engine, dash, insecure, receiver_factory):
    receiver = receiver_factory(200)
    _, wd = setup(dash, receiver.url)
    with SessionLocal() as s:
        delivery.claim_manual(s, dash.id, wd["id"])
    assert claim_ours(wd["id"]) is None


def test_stale_worker_claim_cannot_undo_a_manual_success(engine, dash, insecure, receiver_factory):
    receiver = receiver_factory(500)
    _, wd = setup(dash, receiver.url)
    worker_claim = claim_ours(wd["id"])
    assert worker_claim is not None
    sql(
        engine,
        "UPDATE webhook_deliveries SET locked_until = now() - interval '1 second' WHERE id = :d",
        d=wd["id"],
    )
    receiver.statuses = [200]
    assert resend(dash, wd["id"]).json()["delivery"]["status"] == "succeeded"
    stale = delivery.SendResult(500, None, 5)
    with SessionLocal() as s:
        assert delivery.finish(s, worker_claim, stale) is False
    assert row(engine, wd["id"])["status"] == "succeeded"


def test_failed_manual_resend_keeps_success_and_releases_the_lock(
    engine, dash, insecure, receiver_factory
):
    receiver = receiver_factory(200, 500)
    _, wd = setup(dash, receiver.url)
    assert resend(dash, wd["id"]).json()["delivery"]["status"] == "succeeded"
    r = resend(dash, wd["id"]).json()
    assert r["attempt"]["status_code"] == 500
    after = row(engine, wd["id"])
    assert after["status"] == "succeeded"
    assert after["claim_id"] is None and after["locked_until"] is None
