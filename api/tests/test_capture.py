"""M10: manual and partial capture, and cancelling an uncaptured authorization."""

import re
from urllib.parse import urlencode

import pytest

from app import ledger, seed
from app.ledger_check import ledger_check

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
MANUAL = "amount=1000&currency=usd&capture_method=manual&payment_method=pm_card_visa&confirm=true"
AUTOMATIC = "amount=1000&currency=usd&payment_method=pm_card_visa&confirm=true"


@pytest.fixture(autouse=True)
def _reconciles(db):
    yield
    assert ledger_check(db, own_txn=False) == []


@pytest.fixture
def keys(db):
    return seed.seed(db, "capture-a@example.com")


@pytest.fixture
def skh(keys):
    return {"Authorization": f"Bearer {keys[1]}", **FORM}


@pytest.fixture
def pkh(keys):
    return {"Authorization": f"Bearer {keys[2]}", **FORM}


@pytest.fixture
def other(db):
    _, sk, _ = seed.seed(db, "capture-b@example.com")
    return {"Authorization": f"Bearer {sk}", **FORM}


def create(client, h, body=MANUAL):
    r = client.post("/v1/payment_intents", content=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def authorized(client, h):
    pi = create(client, h)
    assert pi["status"] == "requires_capture" and pi["amount_capturable"] == 1000
    return pi


def capture(client, h, pid, **data):
    return client.post(f"/v1/payment_intents/{pid}/capture", content=urlencode(data), headers=h)


def get(client, h, path):
    r = client.get(path, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def event_types(client, h):
    data = get(client, h, "/v1/events?limit=100")["data"]
    return [e["type"] for e in reversed(data)]


def assert_400(r, code=None, param=None):
    assert r.status_code == 400, r.text
    e = r.json()["error"]
    if code is not None:
        assert e["code"] == code
    if param is not None:
        assert e["param"] == param


def charge_bts(client, h):
    return get(client, h, "/v1/balance_transactions?type=charge")["data"]


def assert_captured(client, db, keys, skh, pi, got, amount):
    assert got["id"] == pi["id"]
    assert got["status"] == "succeeded"
    assert got["amount_received"] == amount
    assert got["amount_capturable"] == 0
    assert get(client, skh, f"/v1/payment_intents/{pi['id']}") == got

    ch = get(client, skh, f"/v1/charges/{got['latest_charge']}")
    assert ch["captured"] is True and ch["amount_captured"] == amount
    assert ch["amount_refunded"] == 0 and ch["refunded"] is False
    assert re.fullmatch(r"txn_[0-9A-Za-z]+", ch["balance_transaction"])

    bt = get(client, skh, f"/v1/balance_transactions/{ch['balance_transaction']}")
    assert bt["amount"] == amount and bt["fee"] == ledger.fee(amount)
    assert bt["type"] == "charge" and bt["status"] == "pending"

    fee = ledger.fee(amount)
    assert ledger.balance(db, keys[0].id)["pending"] == [
        {"amount": amount - fee, "currency": "usd"}
    ]
    assert event_types(client, skh) == [
        "payment_intent.created",
        "charge.succeeded",
        "payment_intent.amount_capturable_updated",
        "charge.captured",
        "payment_intent.succeeded",
    ]


# --- success -------------------------------------------------------------------------------


def test_full_capture_defaults_to_amount_capturable(client, db, keys, skh):
    pi = authorized(client, skh)
    r = capture(client, skh, pi["id"])
    assert r.status_code == 200, r.text
    assert_captured(client, db, keys, skh, pi, r.json(), 1000)


def test_partial_capture(client, db, keys, skh):
    pi = authorized(client, skh)
    r = capture(client, skh, pi["id"], amount_to_capture="600")
    assert r.status_code == 200, r.text
    assert_captured(client, db, keys, skh, pi, r.json(), 600)


def test_capture_exact_amount_capturable(client, db, keys, skh):
    pi = authorized(client, skh)
    r = capture(client, skh, pi["id"], amount_to_capture="1000")
    assert r.status_code == 200, r.text
    assert_captured(client, db, keys, skh, pi, r.json(), 1000)


# --- invalid amount_to_capture -------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "code"),
    [("0", "amount_too_small"), ("1001", "amount_too_large"), ("abc", None)],
)
def test_invalid_amount_to_capture(client, skh, amount, code):
    pi = authorized(client, skh)
    r = capture(client, skh, pi["id"], amount_to_capture=amount)
    assert_400(r, code, param="amount_to_capture")
    after = get(client, skh, f"/v1/payment_intents/{pi['id']}")
    assert after["status"] == "requires_capture" and after["amount_capturable"] == 1000
    assert charge_bts(client, skh) == []


# --- unexpected state ----------------------------------------------------------------------


def test_capture_automatic_pi_is_unexpected_state(client, skh):
    pi = create(client, skh, AUTOMATIC)
    assert pi["status"] == "succeeded"
    assert_400(capture(client, skh, pi["id"]), "payment_intent_unexpected_state")
    assert len(charge_bts(client, skh)) == 1


def test_capture_canceled_pi_is_unexpected_state(client, skh):
    pi = create(client, skh, "amount=1000&currency=usd")
    r = client.post(f"/v1/payment_intents/{pi['id']}/cancel", headers=skh)
    assert r.status_code == 200, r.text
    assert_400(capture(client, skh, pi["id"]), "payment_intent_unexpected_state")


def test_second_capture_is_unexpected_state(client, skh):
    pi = authorized(client, skh)
    assert capture(client, skh, pi["id"], amount_to_capture="600").status_code == 200
    assert_400(capture(client, skh, pi["id"]), "payment_intent_unexpected_state")
    assert len(charge_bts(client, skh)) == 1
    assert get(client, skh, f"/v1/payment_intents/{pi['id']}")["amount_received"] == 600


# --- idempotency and access ----------------------------------------------------------------


def test_idempotent_capture_replays(client, skh):
    pi = authorized(client, skh)
    h = {**skh, "Idempotency-Key": "capture-1"}
    first = capture(client, h, pi["id"], amount_to_capture="600")
    second = capture(client, h, pi["id"], amount_to_capture="600")
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert second.headers["idempotent-replayed"] == "true"
    assert len(charge_bts(client, skh)) == 1
    assert event_types(client, skh).count("charge.captured") == 1


def test_capture_other_merchants_pi_is_404(client, skh, other):
    pi = authorized(client, skh)
    assert capture(client, other, pi["id"]).status_code == 404
    assert get(client, skh, f"/v1/payment_intents/{pi['id']}")["status"] == "requires_capture"


def test_capture_with_publishable_key_is_403(client, skh, pkh):
    pi = authorized(client, skh)
    assert capture(client, pkh, pi["id"]).status_code == 403
    assert get(client, skh, f"/v1/payment_intents/{pi['id']}")["status"] == "requires_capture"


# --- cancel from requires_capture ----------------------------------------------------------


def test_cancel_uncaptured_authorization_voids_it(client, db, keys, skh):
    pi = authorized(client, skh)
    r = client.post(f"/v1/payment_intents/{pi['id']}/cancel", headers=skh)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == "canceled" and got["amount_capturable"] == 0
    ch = get(client, skh, f"/v1/charges/{got['latest_charge']}")
    assert ch["captured"] is False and ch["amount_captured"] == 0
    assert ch["balance_transaction"] is None
    assert get(client, skh, "/v1/balance_transactions")["data"] == []
    assert event_types(client, skh)[-1] == "payment_intent.canceled"
    assert_400(capture(client, skh, pi["id"]), "payment_intent_unexpected_state")
    assert get(client, skh, "/v1/balance_transactions")["data"] == []
