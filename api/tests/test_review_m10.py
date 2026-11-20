"""Regression tests for the M10 capture review: payloads, expiry details, races."""

import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import clock, payment_intents, seed
from app.db import SessionLocal
from app.ids import new_id
from app.ledger_check import ledger_check
from app.main import app

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
AUTH_BODY = (
    "amount=1000&currency=usd&capture_method=manual&payment_method=pm_card_visa&confirm=true"
)


@pytest.fixture
def a(db):
    m, sk, _ = seed.seed(db, "r10@example.com")
    return m, {"Authorization": f"Bearer {sk}", **FORM}


def authorize(client, h):
    pi = client.post("/v1/payment_intents", content=AUTH_BODY, headers=h).json()
    assert pi["status"] == "requires_capture"
    return pi


def last_event(client, h, event_type):
    return client.get(f"/v1/events?type={event_type}", headers=h).json()["data"][0]["data"][
        "object"
    ]


@pytest.mark.parametrize("amount", [None, 600])
def test_capture_event_payloads(client, db, a, amount):
    pi = authorize(client, a[1])
    body = "" if amount is None else f"amount_to_capture={amount}"
    client.post(f"/v1/payment_intents/{pi['id']}/capture", content=body, headers=a[1])
    captured = amount or 1000
    charge = last_event(client, a[1], "charge.captured")
    assert charge["captured"] is True and charge["amount_captured"] == captured
    assert charge["balance_transaction"].startswith("txn_")
    succeeded = last_event(client, a[1], "payment_intent.succeeded")
    assert succeeded["amount_received"] == captured and succeeded["amount_capturable"] == 0
    assert ledger_check(db, own_txn=False) == []


@pytest.mark.parametrize("value", ["-1", ""])
def test_more_invalid_capture_amounts(client, a, value):
    pi = authorize(client, a[1])
    r = client.post(
        f"/v1/payment_intents/{pi['id']}/capture",
        content=f"amount_to_capture={value}",
        headers=a[1],
    )
    assert r.status_code == 400 and r.json()["error"]["param"] == "amount_to_capture"


def test_capture_requires_action_pi_is_unexpected_state(client, a):
    pi = client.post(
        "/v1/payment_intents",
        content="amount=1000&currency=usd&capture_method=manual"
        "&payment_method=pm_card_authenticationRequired&confirm=true"
        "&return_url=https://example.com/r",
        headers=a[1],
    ).json()
    r = client.post(f"/v1/payment_intents/{pi['id']}/capture", headers=a[1])
    assert r.json()["error"]["code"] == "payment_intent_unexpected_state"


def test_expiry_leaves_charge_uncaptured_and_ledger_untouched(client, db, a, monkeypatch):
    pi = authorize(client, a[1])
    later = clock.now() + timedelta(days=7, seconds=1)
    monkeypatch.setattr(clock, "now", lambda: later)
    assert payment_intents.expire_authorizations(db) == 1
    ch = client.get(f"/v1/charges/{pi['latest_charge']}", headers=a[1]).json()
    assert ch["captured"] is False and ch["amount_captured"] == 0
    assert ch["balance_transaction"] is None
    assert client.get("/v1/balance_transactions", headers=a[1]).json()["data"] == []
    assert ledger_check(db, own_txn=False) == []


def test_hold_exactly_seven_days_old_survives(client, db, a, monkeypatch):
    authorize(client, a[1])
    authorized_at = db.execute(text("SELECT max(authorized_at) FROM charges")).scalar()
    monkeypatch.setattr(clock, "now", lambda: authorized_at + timedelta(days=7))
    assert payment_intents.expire_authorizations(db) == 0


# --- races on committed data --------------------------------------------------------------


@pytest.fixture
def committed(engine):
    with SessionLocal.begin() as s:
        m, sk, _ = seed.seed(s, f"{new_id('r10c_')}@example.com")
        merchant_id = m.id
    h = {"Authorization": f"Bearer {sk}", **FORM}
    yield merchant_id, h
    with engine.begin() as conn:
        for table in ("events", "idempotency_keys", "api_keys"):
            conn.execute(text(f"DELETE FROM {table} WHERE merchant_id = :m"), {"m": merchant_id})


def _authorize_committed(h):
    with TestClient(app) as c:
        return authorize(c, h)


def _age_charge(engine, charge_id):
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE charges SET authorized_at = now() - interval '8 days' WHERE id = :c"),
            {"c": charge_id},
        )


def test_expiry_skips_a_locked_pi(committed, engine):
    _, h = committed
    pi = _authorize_committed(h)
    _age_charge(engine, pi["latest_charge"])
    with engine.connect() as locker:
        tx = locker.begin()
        locker.execute(
            text("SELECT 1 FROM payment_intents WHERE id = :p FOR UPDATE"), {"p": pi["id"]}
        )
        with SessionLocal.begin() as s:
            assert payment_intents.expire_authorizations(s) == 0  # skipped, didn't block
        tx.rollback()
    with SessionLocal.begin() as s:
        assert payment_intents.expire_authorizations(s) == 1


@pytest.mark.parametrize("rival", ["expiry", "cancel"])
def test_capture_races_cleanly(committed, engine, rival):
    _, h = committed
    pi = _authorize_committed(h)
    _age_charge(engine, pi["latest_charge"])
    barrier = threading.Barrier(2)
    results = {}

    def capture():
        with TestClient(app) as c:
            barrier.wait()
            results["capture"] = c.post(f"/v1/payment_intents/{pi['id']}/capture", headers=h)

    def other():
        barrier.wait()
        if rival == "expiry":
            with SessionLocal.begin() as s:
                results["other"] = payment_intents.expire_authorizations(s)
        else:
            with TestClient(app) as c:
                results["other"] = c.post(f"/v1/payment_intents/{pi['id']}/cancel", headers=h)

    threads = [threading.Thread(target=capture), threading.Thread(target=other)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    with TestClient(app) as c:
        final = c.get(f"/v1/payment_intents/{pi['id']}", headers=h).json()
        bts = c.get("/v1/balance_transactions?type=charge&limit=100", headers=h).json()["data"]
    mine = [bt for bt in bts if bt["source"] == pi["latest_charge"]]
    if final["status"] == "succeeded":
        assert results["capture"].status_code == 200 and len(mine) == 1
    else:
        assert final["status"] == "canceled" and results["capture"].status_code == 400
        assert mine == []
    assert ledger_check(engine) == []
