"""M10: concurrent captures of one PaymentIntent capture it once (committed data)."""

import threading

from fastapi.testclient import TestClient
from sqlalchemy import text

from app import seed
from app.db import SessionLocal
from app.ids import new_id
from app.ledger_check import ledger_check
from app.main import app

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
MANUAL = "amount=1000&currency=usd&capture_method=manual&payment_method=pm_card_visa&confirm=true"


def test_concurrent_captures_capture_once(engine):
    with SessionLocal.begin() as s:
        m, sk, _ = seed.seed(s, f"{new_id('r10_')}@example.com")
        mid = m.id
    h = {"Authorization": f"Bearer {sk}", **FORM}
    try:
        with TestClient(app) as c:
            r = c.post("/v1/payment_intents", content=MANUAL, headers=h)
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "requires_capture"
            pi, ch = r.json()["id"], r.json()["latest_charge"]
        barrier = threading.Barrier(2)
        codes = []

        def capture():
            with TestClient(app) as c:
                barrier.wait()
                codes.append(c.post(f"/v1/payment_intents/{pi}/capture", headers=h).status_code)

        threads = [threading.Thread(target=capture) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert sorted(codes) == [200, 400]
        with engine.connect() as conn:
            n = conn.execute(
                text(
                    "SELECT count(*) FROM balance_transactions "
                    "WHERE source_id = :c AND type = 'charge'"
                ),
                {"c": ch},
            ).scalar()
        assert n == 1
        assert ledger_check(engine) == []
    finally:
        # Ledger rows, charges, PIs, PMs and the merchant stay: the merchant has money history
        # and the clearing account must keep matching charges' amount_captured.
        with engine.begin() as conn:
            for table in ("events", "idempotency_keys", "api_keys"):
                conn.execute(text(f"DELETE FROM {table} WHERE merchant_id = :m"), {"m": mid})
