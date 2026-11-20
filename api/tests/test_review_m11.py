"""Regression tests for the M11 refunds review."""

import pytest
from sqlalchemy import text

from app import errors, refunds, seed
from app.ledger_check import ledger_check
from app.models import PaymentIntent

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@pytest.fixture
def a(db):
    m, sk, _ = seed.seed(db, "r11@example.com")
    return m, {"Authorization": f"Bearer {sk}", **FORM}


def paid(client, h, amount=1000):
    return client.post(
        "/v1/payment_intents",
        content=f"amount={amount}&currency=usd&payment_method=pm_card_visa&confirm=true",
        headers=h,
    ).json()


def refund(client, h, body):
    return client.post("/v1/refunds", content=body, headers=h)


def test_charge_not_belonging_to_the_locked_pi_is_parameter_invalid(client, db, a, monkeypatch):
    """Defensive path: the locked PI must own the charge (unreachable via the API)."""
    first, second = paid(client, a[1]), paid(client, a[1])
    real_get_owned = refunds.get_owned

    def pi_lookup_returns_the_other_pi(db_, model, object_id, merchant_id, **kw):
        if model is PaymentIntent:
            object_id = second["id"]
        return real_get_owned(db_, model, object_id, merchant_id, **kw)

    monkeypatch.setattr(refunds, "get_owned", pi_lookup_returns_the_other_pi)
    with pytest.raises(errors.ApiError) as exc:
        refunds.create_refund(
            db,
            a[0].id,
            charge_id=first["latest_charge"],
            payment_intent_id=None,
            amount=None,
            reason=None,
            request=None,
        )
    assert exc.value.code == "parameter_invalid" and exc.value.param == "charge"


def test_pi_without_a_charge_says_so(client, a):
    pi = client.post("/v1/payment_intents", content="amount=1000&currency=usd", headers=a[1])
    r = refund(client, a[1], f"payment_intent={pi.json()['id']}")
    assert r.json()["error"]["code"] == "charge_not_captured"
    assert "no captured charge" in r.json()["error"]["message"]


def test_refund_balance_transaction_mismatch_is_reported(client, db, a):
    pi = paid(client, a[1])
    re = refund(client, a[1], f"payment_intent={pi['id']}&amount=300").json()
    db.execute(text("UPDATE refunds SET amount = amount + 1 WHERE id = :r"), {"r": re["id"]})
    assert any("refund doesn't match" in p for p in ledger_check(db, own_txn=False))
    db.execute(text("UPDATE refunds SET amount = amount - 1 WHERE id = :r"), {"r": re["id"]})
    assert ledger_check(db, own_txn=False) == []


def test_partial_refund_emits_charge_refunded_with_partial_amount(client, db, a):
    pi = paid(client, a[1])
    refund(client, a[1], f"charge={pi['latest_charge']}&amount=300")
    events = client.get("/v1/events", headers=a[1]).json()["data"]
    assert [e["type"] for e in events[:2]] == ["charge.refunded", "refund.created"]
    charge = events[0]["data"]["object"]
    assert charge["amount_refunded"] == 300 and charge["refunded"] is False
    assert ledger_check(db, own_txn=False) == []


def test_bad_reason_names_the_param(client, a):
    pi = paid(client, a[1])
    r = refund(client, a[1], f"payment_intent={pi['id']}&reason=bored")
    assert r.status_code == 400 and r.json()["error"]["param"] == "reason"


def test_empty_charge_with_a_pi_refunds_by_pi(client, db, a):
    pi = paid(client, a[1])
    r = refund(client, a[1], f"charge=&payment_intent={pi['id']}&amount=100")
    assert r.status_code == 200 and r.json()["charge"] == pi["latest_charge"]
    assert ledger_check(db, own_txn=False) == []
