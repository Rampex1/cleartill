"""M9b: the Charges API (retrieve, list) and the clearing-vs-charges ledger invariant."""

from urllib.parse import urlencode

import pytest
from sqlalchemy import text

from app import models, seed
from app.ledger_check import ledger_check

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@pytest.fixture
def keys(db):
    return seed.seed(db, "charges-a@example.com")


@pytest.fixture
def skh(keys):
    return {"Authorization": f"Bearer {keys[1]}", **FORM}


@pytest.fixture
def pkh(keys):
    return {"Authorization": f"Bearer {keys[2]}", **FORM}


@pytest.fixture
def other(db):
    _, sk, _ = seed.seed(db, "charges-b@example.com")
    return {"Authorization": f"Bearer {sk}", **FORM}


@pytest.fixture
def ledger_clean(db):
    yield
    assert ledger_check(db, own_txn=False) == []


def paid_pi(client, h, amount="1000"):
    """Create and confirm a PI with pm_card_visa; returns the PI JSON."""
    body = urlencode({"amount": amount, "currency": "usd"})
    r = client.post("/v1/payment_intents", content=body, headers=h)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    r = client.post(
        f"/v1/payment_intents/{pid}/confirm", content="payment_method=pm_card_visa", headers=h
    )
    assert r.status_code == 200, r.text
    return r.json()


def list_ids(client, h, **params):
    r = client.get("/v1/charges", params=params, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["object"] == "list"
    return [c["id"] for c in r.json()["data"]]


def test_charge_model_uses_charges_table():
    assert models.Charge.__tablename__ == "charges"


def test_retrieve_equals_latest_charge(client, skh, ledger_clean):
    pi = paid_pi(client, skh)
    r = client.get(f"/v1/charges/{pi['latest_charge']}", headers=skh)
    assert r.status_code == 200, r.text
    ch = r.json()
    assert ch["id"] == pi["latest_charge"]
    assert ch["payment_intent"] == pi["id"]
    listed = client.get("/v1/charges", headers=skh).json()["data"]
    assert listed == [ch]
    evt = client.get("/v1/events", params={"type": "charge.succeeded"}, headers=skh).json()
    assert evt["data"][0]["data"]["object"] == ch


def test_list_newest_first(client, skh, ledger_clean):
    first = paid_pi(client, skh)["latest_charge"]
    second = paid_pi(client, skh, amount="2000")["latest_charge"]
    assert list_ids(client, skh) == [second, first]


def test_list_payment_intent_filter(client, skh, ledger_clean):
    pi = paid_pi(client, skh)
    paid_pi(client, skh)
    assert list_ids(client, skh, payment_intent=pi["id"]) == [pi["latest_charge"]]


def test_other_merchant_retrieve_is_404(client, skh, other, ledger_clean):
    ch = paid_pi(client, skh)["latest_charge"]
    r = client.get(f"/v1/charges/{ch}", headers=other)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "resource_missing"


def test_other_merchant_list_excludes_charge(client, skh, other, ledger_clean):
    ch = paid_pi(client, skh)["latest_charge"]
    assert ch not in list_ids(client, other)


@pytest.mark.parametrize("path", ["/v1/charges", "/v1/charges/{id}"])
def test_publishable_key_is_403(client, skh, pkh, path, ledger_clean):
    ch = paid_pi(client, skh)["latest_charge"]
    r = client.get(path.format(id=ch), headers=pkh)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "permission_denied"


def test_ledger_check_flags_clearing_vs_captured_mismatch(client, db, skh):
    ch = paid_pi(client, skh)["latest_charge"]
    assert ledger_check(db, own_txn=False) == []
    db.execute(
        text("UPDATE charges SET amount_captured = amount_captured + 1 WHERE id = :c"), {"c": ch}
    )
    assert ledger_check(db, own_txn=False) != []
    db.execute(
        text("UPDATE charges SET amount_captured = amount_captured - 1 WHERE id = :c"), {"c": ch}
    )
    assert ledger_check(db, own_txn=False) == []
