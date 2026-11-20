"""M10: authorizations left uncaptured for 7 days are canceled as abandoned."""

from datetime import timedelta

import pytest

from app import clock, payment_intents, seed
from app.ledger_check import ledger_check

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
MANUAL = "amount=1000&currency=usd&capture_method=manual&payment_method=pm_card_visa&confirm=true"
EXPIRED = timedelta(days=7, seconds=1)


@pytest.fixture(autouse=True)
def _reconciles(db):
    yield
    assert ledger_check(db, own_txn=False) == []


@pytest.fixture
def skh(db):
    _, sk, _ = seed.seed(db, "expiry-a@example.com")
    return {"Authorization": f"Bearer {sk}", **FORM}


def authorize(client, h, body=MANUAL):
    r = client.post("/v1/payment_intents", content=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def retrieve(client, h, pid):
    r = client.get(f"/v1/payment_intents/{pid}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def canceled_events(client, h):
    r = client.get("/v1/events", params={"type": "payment_intent.canceled"}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def advance(monkeypatch, delta):
    later = clock.now() + delta
    monkeypatch.setattr(clock, "now", lambda: later)


def test_authorization_older_than_7_days_is_canceled(client, db, skh, monkeypatch):
    pi = authorize(client, skh)
    advance(monkeypatch, EXPIRED)
    assert payment_intents.expire_authorizations(db) == 1
    got = retrieve(client, skh, pi["id"])
    assert got["status"] == "canceled"
    assert got["cancellation_reason"] == "abandoned"
    assert got["amount_capturable"] == 0
    (e,) = canceled_events(client, skh)
    assert e["request"] == {"id": None, "idempotency_key": None}
    assert e["data"]["object"]["id"] == pi["id"]
    assert e["data"]["object"]["status"] == "canceled"


def test_authorization_6_days_old_is_left_alone(client, db, skh, monkeypatch):
    pi = authorize(client, skh)
    advance(monkeypatch, timedelta(days=6))
    assert payment_intents.expire_authorizations(db) == 0
    got = retrieve(client, skh, pi["id"])
    assert got["status"] == "requires_capture" and got["amount_capturable"] == 1000
    assert canceled_events(client, skh) == []


def test_captured_pi_is_left_alone(client, db, skh, monkeypatch):
    pi = authorize(client, skh)
    r = client.post(f"/v1/payment_intents/{pi['id']}/capture", headers=skh)
    assert r.status_code == 200, r.text
    advance(monkeypatch, EXPIRED)
    assert payment_intents.expire_authorizations(db) == 0
    assert retrieve(client, skh, pi["id"])["status"] == "succeeded"
    assert canceled_events(client, skh) == []


def test_canceled_pi_is_left_alone(client, db, skh, monkeypatch):
    pi = authorize(client, skh)
    r = client.post(f"/v1/payment_intents/{pi['id']}/cancel", headers=skh)
    assert r.status_code == 200, r.text
    advance(monkeypatch, EXPIRED)
    assert payment_intents.expire_authorizations(db) == 0
    got = retrieve(client, skh, pi["id"])
    assert got["status"] == "canceled" and got["cancellation_reason"] is None
    assert len(canceled_events(client, skh)) == 1


def test_only_expired_authorizations_are_counted(client, db, skh, monkeypatch):
    old = [authorize(client, skh)["id"] for _ in range(2)]
    real_now = clock.now
    monkeypatch.setattr(clock, "now", lambda: real_now() + timedelta(days=2))
    fresh = authorize(client, skh)["id"]  # authorized 2 days after the others
    monkeypatch.setattr(clock, "now", lambda: real_now() + EXPIRED)
    assert payment_intents.expire_authorizations(db) == 2
    assert [retrieve(client, skh, p)["status"] for p in old] == ["canceled", "canceled"]
    assert retrieve(client, skh, fresh)["status"] == "requires_capture"
    assert len(canceled_events(client, skh)) == 2
