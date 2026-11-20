"""M9b: concurrent confirms of one PaymentIntent charge it once (committed data)."""

import threading

from fastapi.testclient import TestClient
from sqlalchemy import exc, text

from app import seed
from app.db import SessionLocal
from app.ids import new_id
from app.ledger_check import ledger_check
from app.main import app

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def test_concurrent_confirms_charge_once(engine):
    with SessionLocal.begin() as s:
        m, sk, _ = seed.seed(s, f"{new_id('r9b_')}@example.com")
        mid = m.id
    h = {"Authorization": f"Bearer {sk}", **FORM}
    try:
        with TestClient(app) as c:
            r = c.post(
                "/v1/payment_intents",
                content="amount=1000&currency=usd&payment_method=pm_card_visa",
                headers=h,
            )
            assert r.status_code == 200, r.text
            pi = r.json()["id"]
        barrier = threading.Barrier(2)
        codes = []

        def confirm():
            with TestClient(app) as c:
                barrier.wait()
                codes.append(c.post(f"/v1/payment_intents/{pi}/confirm", headers=h).status_code)

        threads = [threading.Thread(target=confirm) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert sorted(codes) == [200, 400]
        with TestClient(app) as c:
            r = c.get("/v1/charges", params={"payment_intent": pi}, headers=h)
        assert r.status_code == 200, r.text
        assert len(r.json()["data"]) == 1
        assert ledger_check(engine) == []
    finally:
        # Charges, PIs and PMs stay: the clearing account must keep matching charges'
        # amount_captured. The merchant can only go if it never got a balance transaction.
        with engine.begin() as conn:
            for table in ("events", "idempotency_keys", "api_keys"):
                conn.execute(text(f"DELETE FROM {table} WHERE merchant_id = :m"), {"m": mid})
        try:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM merchants WHERE id = :m"), {"m": mid})
        except exc.IntegrityError:
            pass
