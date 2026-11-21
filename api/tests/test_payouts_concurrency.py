"""M23: two simultaneous payouts can't overdraw the available balance (committed data; ledger
rows stay behind, as CLAUDE.md allows)."""

from sqlalchemy import text

from app import ledger, seed
from app.db import SessionLocal
from app.ids import new_id
from app.ledger_check import ledger_check
from tests.test_review_m16 import race

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def committed_funded_merchant():
    """A committed merchant with 10000 - fee(10000) available."""
    with SessionLocal.begin() as s:
        m, sk, _ = seed.seed(s, f"{new_id('po_').lower()}@example.com")
        ledger.release(s, ledger.post_capture(s, m.id, "ch_po_race", 10000))
        return m.id, sk, 10000 - ledger.fee(10000)


def test_two_simultaneous_payouts_cannot_overdraw(engine):
    mid, sk, available = committed_funded_merchant()
    h = {"Authorization": f"Bearer {sk}", **FORM}
    amount = int(available * 0.6)

    def pay_out(c):
        return c.post("/v1/payouts", content=f"amount={amount}&currency=usd", headers=h)

    results = race(pay_out, pay_out)
    assert sorted(r.status_code for r in results) == [200, 400], [r.text for r in results]
    loser = next(r for r in results if r.status_code == 400)
    assert loser.json()["error"]["code"] == "balance_insufficient"
    with SessionLocal() as s:
        count = s.scalar(text("SELECT count(*) FROM payouts WHERE merchant_id = :m"), {"m": mid})
        left = ledger.balance(s, mid)["available"][0]["amount"]
    assert count == 1
    assert left == available - amount
    assert ledger_check(engine) == []
