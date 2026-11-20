"""M14: the delivery worker (claim, send, finish), backoff, give-up and the attempt log.

The worker commits through its own sessions, so these tests use committed data and clean
up after themselves. deliver_one()/claim() pick any due row globally: every assertion is
about this test's own merchant's delivery."""

import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import clock, delivery, seed, webhooks
from app.db import SessionLocal
from app.ids import new_id
from app.main import app

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
CLEANUP = (
    "webhook_attempts",
    "webhook_deliveries",
    "events",
    "webhook_endpoints",
    "idempotency_keys",
    "api_keys",
    "customers",
    "ledger_accounts",
)


class Receiver:
    """Local HTTP receiver: records each request, replies from a scripted status list
    (the last status repeats once the script runs out)."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.requests = []
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                receiver.requests.append(
                    {"path": self.path, "headers": dict(self.headers), "body": body.decode()}
                )
                status = (
                    receiver.statuses.pop(0) if len(receiver.statuses) > 1 else receiver.statuses[0]
                )
                self.send_response(status)
                if 300 <= status < 400:
                    self.send_header("Location", f"{receiver.url}redirected")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            do_GET = do_POST

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def receiver_factory():
    made = []

    def make(*statuses):
        r = Receiver(statuses)
        made.append(r)
        return r

    yield make
    for r in made:
        r.close()


@pytest.fixture
def insecure(monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_URLS", "1")


@pytest.fixture
def merchant(engine):
    with SessionLocal.begin() as s:
        m, sk, _ = seed.seed(s, f"{new_id('w14_')}@example.com")
        merchant_id = m.id
    try:
        yield merchant_id, {"Authorization": f"Bearer {sk}", **FORM}
    finally:
        with engine.begin() as conn:
            for table in CLEANUP:
                conn.execute(
                    text(f"DELETE FROM {table} WHERE merchant_id = :m"), {"m": merchant_id}
                )
            conn.execute(text("DELETE FROM merchants WHERE id = :m"), {"m": merchant_id})


def setup_delivery(merchant, url):
    """Create an endpoint at url and a customer (one pending delivery). Returns
    (endpoint json incl. secret, delivery id)."""
    merchant_id, h = merchant
    with TestClient(app) as c:
        r = c.post("/v1/webhook_endpoints", content=f"url={url}&enabled_events[]=*", headers=h)
        assert r.status_code == 200, r.text
        ep = r.json()
        assert c.post("/v1/customers", headers=h).status_code == 200
    with SessionLocal() as s:
        (delivery_id,) = s.execute(
            text("SELECT id FROM webhook_deliveries WHERE merchant_id = :m"), {"m": merchant_id}
        ).scalars()
    return ep, delivery_id


def row(engine, delivery_id):
    with engine.connect() as conn:
        return (
            conn.execute(text("SELECT * FROM webhook_deliveries WHERE id = :d"), {"d": delivery_id})
            .mappings()
            .one()
        )


def attempts(engine, delivery_id):
    with engine.connect() as conn:
        return (
            conn.execute(
                text("SELECT * FROM webhook_attempts WHERE delivery_id = :d ORDER BY at, id"),
                {"d": delivery_id},
            )
            .mappings()
            .all()
        )


def sql(engine, stmt, **params):
    with engine.begin() as conn:
        conn.execute(text(stmt), params)


def deliver_until_attempted(engine, delivery_id, limit=50):
    """Run deliver_one() until our delivery gets one more attempt row (other committed
    rows may be due too and get worked first)."""
    before = len(attempts(engine, delivery_id))
    for _ in range(limit):
        if not delivery.deliver_one():
            break
        if len(attempts(engine, delivery_id)) > before:
            return
    raise AssertionError("our delivery was not attempted")


def claim_ours(delivery_id, limit=50):
    """claim() until it returns our delivery; None if it never does."""
    for _ in range(limit):
        with SessionLocal() as s:
            c = delivery.claim(s)
        if c is None:
            return None
        if c.delivery_id == delivery_id:
            return c
    return None


def test_constants():
    assert delivery.BACKOFF == [60, 300, 1800, 7200, 18000, 36000]
    assert delivery.GIVE_UP_AFTER == timedelta(hours=72)


# --- end to end ----------------------------------------------------------------------------


def test_end_to_end_retry_then_success(engine, merchant, insecure, receiver_factory):
    rx = receiver_factory(500, 200)
    ep, d = setup_delivery(merchant, rx.url)

    deliver_until_attempted(engine, d)
    first = row(engine, d)
    assert first["status"] == "pending" and first["attempts"] == 1
    assert first["locked_until"] is None

    sql(
        engine,
        "UPDATE webhook_deliveries SET next_attempt_at = now() - interval '1 second' WHERE id = :d",
        d=d,
    )
    deliver_until_attempted(engine, d)

    final = row(engine, d)
    assert final["status"] == "succeeded"
    assert final["attempts"] == 1
    assert final["locked_until"] is None

    log = attempts(engine, d)
    assert [a["status_code"] for a in log] == [500, 200]
    assert all(a["merchant_id"] == merchant[0] for a in log)
    assert all(a["error"] is None for a in log)
    assert all(a["manual"] is False for a in log)
    assert all(a["duration_ms"] is not None and a["duration_ms"] >= 0 for a in log)

    assert len(rx.requests) == 2
    for req in rx.requests:
        event = json.loads(req["body"])
        assert event["type"] == "customer.created"
        assert event["object"] == "event"
        assert event["data"]["object"]["object"] == "customer"
        assert req["headers"]["Content-Type"] == "application/json"
        assert req["headers"]["Host"] == f"127.0.0.1:{rx.port}"
        assert webhooks.verify(req["body"], req["headers"][webhooks.SIGNATURE_HEADER], ep["secret"])
    assert json.loads(rx.requests[0]["body"])["id"] == json.loads(rx.requests[1]["body"])["id"]


# --- backoff and give-up -------------------------------------------------------------------


def test_backoff_follows_schedule_on_fake_clock(
    engine, merchant, insecure, receiver_factory, monkeypatch
):
    rx = receiver_factory(500)
    _, d = setup_delivery(merchant, rx.url)
    now = datetime.now(UTC)
    for i in range(len(delivery.BACKOFF) + 1):
        monkeypatch.setattr(clock, "now", lambda t=now: t)
        deliver_until_attempted(engine, d)
        r = row(engine, d)
        assert r["status"] == "pending"
        assert r["attempts"] == i + 1
        expected = now + timedelta(seconds=delivery.BACKOFF[min(i, 5)])
        assert abs((r["next_attempt_at"] - expected).total_seconds()) < 1
        now = r["next_attempt_at"] + timedelta(seconds=1)
    assert [a["status_code"] for a in attempts(engine, d)] == [500] * (len(delivery.BACKOFF) + 1)


def test_not_retried_before_backoff_elapses(
    engine, merchant, insecure, receiver_factory, monkeypatch
):
    rx = receiver_factory(500)
    _, d = setup_delivery(merchant, rx.url)
    now = datetime.now(UTC)
    monkeypatch.setattr(clock, "now", lambda: now)
    deliver_until_attempted(engine, d)
    monkeypatch.setattr(clock, "now", lambda: now + timedelta(seconds=59))
    assert claim_ours(d) is None
    assert len(rx.requests) == 1


def test_gives_up_after_72_hours(engine, merchant, insecure, receiver_factory):
    rx = receiver_factory(500)
    _, d = setup_delivery(merchant, rx.url)
    sql(
        engine,
        "UPDATE webhook_deliveries SET created_at = now() - interval '73 hours' WHERE id = :d",
        d=d,
    )
    deliver_until_attempted(engine, d)
    r = row(engine, d)
    assert r["status"] == "failed"
    assert r["locked_until"] is None
    assert [a["status_code"] for a in attempts(engine, d)] == [500]


def test_success_after_72_hours_still_succeeds(engine, merchant, insecure, receiver_factory):
    rx = receiver_factory(200)
    _, d = setup_delivery(merchant, rx.url)
    sql(
        engine,
        "UPDATE webhook_deliveries SET created_at = now() - interval '73 hours' WHERE id = :d",
        d=d,
    )
    deliver_until_attempted(engine, d)
    assert row(engine, d)["status"] == "succeeded"


# --- send ----------------------------------------------------------------------------------


def test_redirect_is_a_failure_and_not_followed(engine, merchant, insecure, receiver_factory):
    rx = receiver_factory(302)
    _, d = setup_delivery(merchant, rx.url)
    deliver_until_attempted(engine, d)
    (a,) = attempts(engine, d)
    assert a["status_code"] == 302
    r = row(engine, d)
    assert r["status"] == "pending" and r["attempts"] == 1
    assert len(rx.requests) == 1


def test_blocked_url_is_never_requested(engine, merchant, insecure, receiver_factory, monkeypatch):
    rx = receiver_factory(200)
    _, d = setup_delivery(merchant, rx.url)
    monkeypatch.delenv("ALLOW_INSECURE_URLS")
    deliver_until_attempted(engine, d)
    (a,) = attempts(engine, d)
    assert a["status_code"] is None
    assert a["error"] == "url_blocked"
    assert row(engine, d)["status"] != "succeeded"
    assert rx.requests == []


def test_send_blocked_url_makes_no_request(receiver_factory, monkeypatch):
    monkeypatch.delenv("ALLOW_INSECURE_URLS", raising=False)
    rx = receiver_factory(200)
    result = delivery.send(rx.url, "{}", {"Content-Type": "application/json"})
    assert result.status_code is None
    assert result.error == "url_blocked"
    assert result.ok is False
    assert rx.requests == []


def test_send_ok_only_for_2xx(insecure, receiver_factory):
    rx = receiver_factory(204, 299, 400, 503)
    results = [delivery.send(rx.url, "{}", {"Content-Type": "application/json"}) for _ in range(4)]
    assert [r.status_code for r in results] == [204, 299, 400, 503]
    assert [r.ok for r in results] == [True, True, False, False]
    assert all(r.error is None and r.duration_ms >= 0 for r in results)


def test_send_connection_refused_records_error(insecure):
    rx = Receiver([200])
    url = rx.url
    rx.close()
    result = delivery.send(url, "{}", {"Content-Type": "application/json"})
    assert result.status_code is None
    assert result.ok is False
    assert result.error


# --- claim rules ---------------------------------------------------------------------------


def test_disabled_endpoint_delivery_is_not_claimed(engine, merchant, insecure, receiver_factory):
    rx = receiver_factory(200)
    ep, d = setup_delivery(merchant, rx.url)
    sql(engine, "UPDATE webhook_endpoints SET status = 'disabled' WHERE id = :e", e=ep["id"])
    assert claim_ours(d) is None
    assert row(engine, d)["claim_id"] is None


def test_deleted_endpoint_delivery_is_not_claimed(engine, merchant, insecure, receiver_factory):
    rx = receiver_factory(200)
    ep, d = setup_delivery(merchant, rx.url)
    sql(engine, "UPDATE webhook_endpoints SET deleted = true WHERE id = :e", e=ep["id"])
    assert claim_ours(d) is None


def test_locked_delivery_is_not_claimed(engine, merchant, insecure, receiver_factory):
    rx = receiver_factory(200)
    _, d = setup_delivery(merchant, rx.url)
    sql(
        engine,
        "UPDATE webhook_deliveries SET locked_until = now() + interval '1 hour' WHERE id = :d",
        d=d,
    )
    assert claim_ours(d) is None


def test_not_yet_due_delivery_is_not_claimed(engine, merchant, insecure, receiver_factory):
    rx = receiver_factory(200)
    _, d = setup_delivery(merchant, rx.url)
    sql(
        engine,
        "UPDATE webhook_deliveries SET next_attempt_at = now() + interval '1 hour' WHERE id = :d",
        d=d,
    )
    assert claim_ours(d) is None


def test_succeeded_delivery_is_not_claimed(engine, merchant, insecure, receiver_factory):
    rx = receiver_factory(200)
    _, d = setup_delivery(merchant, rx.url)
    sql(engine, "UPDATE webhook_deliveries SET status = 'succeeded' WHERE id = :d", d=d)
    assert claim_ours(d) is None


def test_claim_returns_signing_inputs_and_locks(engine, merchant, insecure, receiver_factory):
    rx = receiver_factory(200)
    ep, d = setup_delivery(merchant, rx.url)
    before = datetime.now(UTC)
    c = claim_ours(d)
    assert c is not None
    assert c.url == rx.url
    assert c.secret == ep["secret"]
    event = json.loads(c.payload)
    assert event["type"] == "customer.created" and event["object"] == "event"
    r = row(engine, d)
    assert r["claim_id"] == c.claim_id
    lock = (r["locked_until"] - before).total_seconds()
    assert 115 <= lock <= 130
    assert claim_ours(d) is None  # locked now


# --- fencing -------------------------------------------------------------------------------


def test_stale_claim_cannot_finish(engine, merchant, insecure, receiver_factory):
    rx = receiver_factory(201, 200)
    _, d = setup_delivery(merchant, rx.url)

    a = claim_ours(d)
    assert a is not None
    sql(
        engine,
        "UPDATE webhook_deliveries SET locked_until = now() - interval '1 second' WHERE id = :d",
        d=d,
    )
    b = claim_ours(d)
    assert b is not None and b.claim_id != a.claim_id

    headers = {"Content-Type": "application/json"}
    result_a = delivery.send(a.url, a.payload, headers)
    result_b = delivery.send(b.url, b.payload, headers)
    assert result_a.status_code == 201 and result_b.status_code == 200

    with SessionLocal() as s:
        assert delivery.finish(s, a, result_a) is False
    with SessionLocal() as s:
        assert delivery.finish(s, b, result_b) is True

    r = row(engine, d)
    assert r["status"] == "succeeded"
    assert r["locked_until"] is None
    assert [x["status_code"] for x in attempts(engine, d)] == [200]


def test_finish_after_success_is_stale(engine, merchant, insecure, receiver_factory):
    rx = receiver_factory(200)
    _, d = setup_delivery(merchant, rx.url)
    c = claim_ours(d)
    result = delivery.send(c.url, c.payload, {"Content-Type": "application/json"})
    with SessionLocal() as s:
        assert delivery.finish(s, c, result) is True
    with SessionLocal() as s:
        assert delivery.finish(s, c, result) is False
    assert len(attempts(engine, d)) == 1
