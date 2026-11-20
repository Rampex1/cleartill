"""M11: concurrent refunds of one charge, on committed data, can't over-refund it."""

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import seed
from app.db import SessionLocal
from app.ids import new_id
from app.ledger_check import ledger_check
from app.main import app

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
PAY = "amount=1000&currency=usd&payment_method=pm_card_visa&confirm=true"


@pytest.fixture
def committed(engine):
    with SessionLocal.begin() as s:
        m, sk, _ = seed.seed(s, f"{new_id('r11c_')}@example.com")
        merchant_id = m.id
    h = {"Authorization": f"Bearer {sk}", **FORM}
    yield merchant_id, h
    with engine.begin() as conn:
        for table in ("events", "idempotency_keys", "api_keys"):
            conn.execute(text(f"DELETE FROM {table} WHERE merchant_id = :m"), {"m": merchant_id})


def test_concurrent_refunds_by_charge_and_pi_cannot_over_refund(committed, engine):
    _, h = committed
    with TestClient(app) as c:
        pi = c.post("/v1/payment_intents", content=PAY, headers=h).json()
    assert pi["status"] == "succeeded"
    ch_id = pi["latest_charge"]
    barrier = threading.Barrier(2)
    results = {}

    def run(name, body):
        with TestClient(app) as c:
            barrier.wait()
            results[name] = c.post("/v1/refunds", content=body, headers=h)

    threads = [
        threading.Thread(target=run, args=("charge", f"amount=800&charge={ch_id}")),
        threading.Thread(target=run, args=("pi", f"amount=800&payment_intent={pi['id']}")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)

    codes = sorted(r.status_code for r in results.values())
    assert codes == [200, 400], [r.text for r in results.values()]
    loser = next(r for r in results.values() if r.status_code == 400)
    assert loser.json()["error"]["code"] == "amount_too_large"
    with TestClient(app) as c:
        listed = c.get(f"/v1/refunds?charge={ch_id}", headers=h).json()["data"]
        ch = c.get(f"/v1/charges/{ch_id}", headers=h).json()
    assert len(listed) == 1
    assert ch["amount_refunded"] == 800 and ch["refunded"] is False
    assert ledger_check(engine) == []
