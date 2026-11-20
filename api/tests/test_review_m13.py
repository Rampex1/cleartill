"""Regression tests for the M13 webhook review: SSRF forms, resolver bounds, the
emit-vs-disable race, delete semantics and pending_webhooks."""

import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import errors, seed, urls
from app.db import SessionLocal
from app.events import emit
from app.ids import new_id
from app.main import app
from app.models import WebhookDelivery

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
PUBLIC = "https://93.184.216.34/hook"


@pytest.fixture(autouse=True)
def _secure(monkeypatch):
    monkeypatch.delenv("ALLOW_INSECURE_URLS", raising=False)


@pytest.mark.parametrize(
    "url",
    [
        "http://[64:ff9b::7f00:1]/",
        "http://[64:ff9b::a00:1]/",
        "http://[::127.0.0.1]/",
        "http://0177.0.0.1/",
        "http://0x7f.1/",
        "http://2130706433/",
        "http://127.1/",
        "http://93.184.216.34:0/",
    ],
)
def test_more_unsafe_forms_are_rejected(url):
    with pytest.raises(errors.ApiError) as exc:
        urls.validate_webhook_url(url)
    assert exc.value.code == "url_invalid"


def test_insecure_mode_still_returns_ips_for_pinning(monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_URLS", "1")
    monkeypatch.setattr(urls, "_resolve", lambda host, port: ["10.0.0.5"])
    assert urls.validate_webhook_url("http://internal.test/hook") == ["10.0.0.5"]


def test_busy_resolver_fails_fast(monkeypatch):
    monkeypatch.setattr(urls, "_resolve", lambda host, port: ["93.184.216.34"])
    taken = [urls._resolver_slots.acquire(blocking=False) for _ in range(urls.RESOLVER_THREADS)]
    try:
        started = time.monotonic()
        with pytest.raises(errors.ApiError):
            urls.validate_webhook_url("https://example.test/")
        assert time.monotonic() - started < 1
    finally:
        for ok in taken:
            if ok:
                urls._resolver_slots.release()
    assert urls.validate_webhook_url("https://example.test/") == ["93.184.216.34"]


def test_slow_lookup_times_out_and_frees_its_slot(monkeypatch):
    monkeypatch.setattr(urls, "RESOLVE_TIMEOUT_SECONDS", 0.2)
    release = threading.Event()

    def slow(host, port):
        release.wait(5)
        return ["93.184.216.34"]

    monkeypatch.setattr(urls, "_resolve", slow)
    with pytest.raises(errors.ApiError):
        urls.validate_webhook_url("https://slow.test/")
    release.set()
    monkeypatch.setattr(urls, "_resolve", lambda host, port: ["93.184.216.34"])
    assert urls.validate_webhook_url("https://fast.test/") == ["93.184.216.34"]


@pytest.fixture
def merchant(db):
    m, sk, _ = seed.seed(db, "w13@example.com")
    return m, {"Authorization": f"Bearer {sk}", **FORM}


def create_endpoint(client, h, events="*"):
    return client.post(
        "/v1/webhook_endpoints", content=f"url={PUBLIC}&enabled_events[]={events}", headers=h
    ).json()


def test_delete_also_disables(client, db, merchant):
    ep = create_endpoint(client, merchant[1])
    client.delete(f"/v1/webhook_endpoints/{ep['id']}", headers=merchant[1])
    status = db.execute(
        text("SELECT status FROM webhook_endpoints WHERE id = :e"), {"e": ep["id"]}
    ).scalar()
    assert status == "disabled"


def test_star_and_specific_type_create_one_row(client, db, merchant):
    client.post(
        "/v1/webhook_endpoints",
        content=f"url={PUBLIC}&enabled_events[]=*&enabled_events[]=customer.created",
        headers=merchant[1],
    )
    client.post("/v1/customers", headers=merchant[1])
    rows = db.scalars(select(WebhookDelivery).where(WebhookDelivery.merchant_id == merchant[0].id))
    assert len(rows.all()) == 1


def test_pending_webhooks_ignores_succeeded(client, db, merchant):
    create_endpoint(client, merchant[1])
    client.post("/v1/customers", headers=merchant[1])
    db.execute(
        text("UPDATE webhook_deliveries SET status = 'succeeded' WHERE merchant_id = :m"),
        {"m": merchant[0].id},
    )
    event = client.get("/v1/events", headers=merchant[1]).json()["data"][0]
    assert event["pending_webhooks"] == 0


def test_emit_racing_a_disable_leaves_no_pending_row(engine):
    """A disable is in flight (endpoint row locked, status already 'disabled', not yet
    committed) when an emit runs. The emit must wait on the endpoint row and then skip it;
    reading the old committed 'enabled' row would strand a pending delivery forever."""
    with SessionLocal.begin() as s:
        m, sk, _ = seed.seed(s, f"{new_id('w13r_')}@example.com")
        merchant_id = m.id
    h = {"Authorization": f"Bearer {sk}", **FORM}
    try:
        with TestClient(app) as c:
            ep = create_endpoint(c, h)
        result = {}
        with engine.connect() as disabler:
            tx = disabler.begin()
            disabler.execute(
                text("SELECT 1 FROM webhook_endpoints WHERE id = :e FOR UPDATE"), {"e": ep["id"]}
            )
            disabler.execute(
                text("UPDATE webhook_endpoints SET status = 'disabled' WHERE id = :e"),
                {"e": ep["id"]},
            )

            def run_emit():
                with SessionLocal.begin() as s:
                    result["event"] = emit(s, merchant_id, "customer.created", {"id": "x"}).id

            t = threading.Thread(target=run_emit)
            t.start()
            time.sleep(0.5)
            assert t.is_alive(), "emit should wait on the endpoint row"
            tx.commit()
            t.join(10)
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT status FROM webhook_deliveries WHERE event_id = :e"),
                {"e": result["event"]},
            ).all()
        assert rows == []
    finally:
        with engine.begin() as conn:
            for table in ("webhook_deliveries", "events", "idempotency_keys", "api_keys"):
                conn.execute(
                    text(f"DELETE FROM {table} WHERE merchant_id = :m"), {"m": merchant_id}
                )
            conn.execute(
                text("DELETE FROM webhook_endpoints WHERE merchant_id = :m"), {"m": merchant_id}
            )
