"""M11: refunds (full and partial), their balance transactions, events and reconciliation."""

import re
from urllib.parse import urlencode

import pytest
from sqlalchemy import text

from app import seed
from app.ledger_check import ledger_check

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
AUTOMATIC = "amount=1000&currency=usd&payment_method=pm_card_visa&confirm=true"
MANUAL = "amount=1000&currency=usd&capture_method=manual&payment_method=pm_card_visa&confirm=true"
REFUND_KEYS = {
    "id",
    "object",
    "amount",
    "charge",
    "payment_intent",
    "status",
    "reason",
    "balance_transaction",
    "currency",
    "created",
    "livemode",
}


@pytest.fixture(autouse=True)
def _reconciles(db):
    yield
    assert ledger_check(db, own_txn=False) == []


@pytest.fixture
def keys(db):
    return seed.seed(db, "refunds-a@example.com")


@pytest.fixture
def skh(keys):
    return {"Authorization": f"Bearer {keys[1]}", **FORM}


@pytest.fixture
def pkh(keys):
    return {"Authorization": f"Bearer {keys[2]}", **FORM}


@pytest.fixture
def other(db):
    _, sk, _ = seed.seed(db, "refunds-b@example.com")
    return {"Authorization": f"Bearer {sk}", **FORM}


def create_pi(client, h, body=AUTOMATIC):
    r = client.post("/v1/payment_intents", content=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def paid(client, h):
    pi = create_pi(client, h)
    assert pi["status"] == "succeeded"
    return pi


def refund(client, h, **data):
    return client.post("/v1/refunds", content=urlencode(data), headers=h)


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def get(client, h, path):
    return ok(client.get(path, headers=h))


def charge(client, h, ch_id):
    return get(client, h, f"/v1/charges/{ch_id}")


def available(client, h):
    return get(client, h, "/v1/balance")["available"][0]["amount"]


def events(client, h):
    return list(reversed(get(client, h, "/v1/events?limit=100")["data"]))


def assert_400(r, code=None, param=None):
    assert r.status_code == 400, r.text
    e = r.json()["error"]
    if code is not None:
        assert e["code"] == code
    if param is not None:
        assert e["param"] == param


def refund_bts(client, h):
    return get(client, h, "/v1/balance_transactions?type=refund")["data"]


# --- success -------------------------------------------------------------------------------


def test_full_refund_by_payment_intent(client, skh):
    pi = paid(client, skh)
    ch_id = pi["latest_charge"]
    re_ = ok(refund(client, skh, payment_intent=pi["id"]))

    assert set(re_) == REFUND_KEYS
    assert re.fullmatch(r"re_[0-9A-Za-z]+", re_["id"])
    assert re_["object"] == "refund"
    assert re_["amount"] == 1000
    assert re_["charge"] == ch_id
    assert re_["payment_intent"] == pi["id"]
    assert re_["status"] == "succeeded"
    assert re_["reason"] is None
    assert re.fullmatch(r"txn_[0-9A-Za-z]+", re_["balance_transaction"])
    assert re_["currency"] == "usd"
    assert isinstance(re_["created"], int)
    assert re_["livemode"] is False

    ch = charge(client, skh, ch_id)
    assert ch["amount_refunded"] == 1000 and ch["refunded"] is True

    bt = get(client, skh, f"/v1/balance_transactions/{re_['balance_transaction']}")
    assert bt["type"] == "refund"
    assert bt["amount"] == -1000 and bt["fee"] == 0 and bt["net"] == -1000
    assert bt["status"] == "available"

    assert available(client, skh) == -1000

    evs = events(client, skh)
    types = [e["type"] for e in evs]
    assert types[-2:] == ["refund.created", "charge.refunded"]
    assert evs[-2]["data"]["object"] == re_
    refunded = evs[-1]["data"]["object"]
    assert refunded["id"] == ch_id and refunded["amount_refunded"] == 1000
    assert refunded["refunded"] is True


def test_retrieve_refund(client, skh):
    pi = paid(client, skh)
    re_ = ok(refund(client, skh, payment_intent=pi["id"]))
    assert get(client, skh, f"/v1/refunds/{re_['id']}") == re_


def test_two_partial_refunds_by_charge(client, skh):
    ch_id = paid(client, skh)["latest_charge"]

    first = ok(refund(client, skh, charge=ch_id, amount="300"))
    assert first["amount"] == 300
    ch = charge(client, skh, ch_id)
    assert ch["amount_refunded"] == 300 and ch["refunded"] is False
    assert available(client, skh) == -300

    second = ok(refund(client, skh, charge=ch_id, amount="700"))
    assert second["amount"] == 700
    ch = charge(client, skh, ch_id)
    assert ch["amount_refunded"] == 1000 and ch["refunded"] is True
    assert available(client, skh) == -1000

    assert_400(refund(client, skh, charge=ch_id), "charge_already_refunded")
    assert_400(refund(client, skh, charge=ch_id, amount="1"), "charge_already_refunded")
    assert len(refund_bts(client, skh)) == 2


def test_default_amount_refunds_the_remainder(client, skh):
    ch_id = paid(client, skh)["latest_charge"]
    ok(refund(client, skh, charge=ch_id, amount="250"))
    rest = ok(refund(client, skh, charge=ch_id))
    assert rest["amount"] == 750
    assert charge(client, skh, ch_id)["refunded"] is True


@pytest.mark.parametrize("reason", ["duplicate", "fraudulent", "requested_by_customer"])
def test_valid_reason_is_returned(client, skh, reason):
    pi = paid(client, skh)
    assert ok(refund(client, skh, payment_intent=pi["id"], reason=reason))["reason"] == reason


def test_empty_reason_means_none(client, skh):
    pi = paid(client, skh)
    assert ok(refund(client, skh, payment_intent=pi["id"], reason=""))["reason"] is None


# --- invalid input -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "code"), [("0", "amount_too_small"), ("1001", "amount_too_large")]
)
def test_invalid_amount(client, skh, amount, code):
    pi = paid(client, skh)
    assert_400(refund(client, skh, charge=pi["latest_charge"], amount=amount), code, "amount")
    assert charge(client, skh, pi["latest_charge"])["amount_refunded"] == 0
    assert refund_bts(client, skh) == []


def test_amount_too_large_after_partial(client, skh):
    ch_id = paid(client, skh)["latest_charge"]
    ok(refund(client, skh, charge=ch_id, amount="400"))
    assert_400(refund(client, skh, charge=ch_id, amount="601"), "amount_too_large", "amount")
    assert charge(client, skh, ch_id)["amount_refunded"] == 400


def test_bad_reason(client, skh):
    pi = paid(client, skh)
    assert_400(refund(client, skh, payment_intent=pi["id"], reason="changed_my_mind"))
    assert refund_bts(client, skh) == []


def test_no_target_is_parameter_missing(client, skh):
    assert_400(refund(client, skh, amount="100"), "parameter_missing", "charge")


def test_both_targets_is_parameter_invalid(client, skh):
    pi = paid(client, skh)
    r = refund(client, skh, charge=pi["latest_charge"], payment_intent=pi["id"])
    assert_400(r, "parameter_invalid")
    assert refund_bts(client, skh) == []


@pytest.mark.parametrize(("param", "value"), [("charge", "ch_nope"), ("payment_intent", "pi_nope")])
def test_unknown_target_is_404(client, skh, param, value):
    r = refund(client, skh, **{param: value})
    assert r.status_code == 404, r.text
    e = r.json()["error"]
    assert e["code"] == "resource_missing" and e["param"] == param


# --- not captured --------------------------------------------------------------------------


def test_uncaptured_manual_pi_by_payment_intent(client, skh):
    pi = create_pi(client, skh, MANUAL)
    assert pi["status"] == "requires_capture"
    assert_400(refund(client, skh, payment_intent=pi["id"]), "charge_not_captured")


def test_uncaptured_manual_pi_by_charge(client, skh):
    pi = create_pi(client, skh, MANUAL)
    assert_400(refund(client, skh, charge=pi["latest_charge"]), "charge_not_captured")
    assert charge(client, skh, pi["latest_charge"])["amount_refunded"] == 0


def test_failed_charge_of_declined_pi(client, skh):
    r = client.post(
        "/v1/payment_intents",
        content="amount=1000&currency=usd&payment_method=pm_card_visa_chargeDeclined&confirm=true",
        headers=skh,
    )
    assert r.status_code == 402, r.text
    ch_id = r.json()["error"]["payment_intent"]["latest_charge"]
    assert ch_id
    assert_400(refund(client, skh, charge=ch_id), "charge_not_captured")


def test_partial_capture_limits_refundable_amount(client, skh):
    pi = create_pi(client, skh, MANUAL)
    r = client.post(
        f"/v1/payment_intents/{pi['id']}/capture", content="amount_to_capture=600", headers=skh
    )
    assert r.status_code == 200, r.text
    ch_id = pi["latest_charge"]
    assert_400(refund(client, skh, charge=ch_id, amount="601"), "amount_too_large", "amount")
    assert ok(refund(client, skh, charge=ch_id, amount="600"))["amount"] == 600
    ch = charge(client, skh, ch_id)
    assert ch["amount_refunded"] == 600 and ch["refunded"] is True


# --- tenancy and access --------------------------------------------------------------------


@pytest.mark.parametrize("param", ["charge", "payment_intent"])
def test_other_merchants_target_is_404(client, skh, other, param):
    pi = paid(client, skh)
    value = pi["latest_charge"] if param == "charge" else pi["id"]
    r = refund(client, other, **{param: value})
    assert r.status_code == 404, r.text
    e = r.json()["error"]
    assert e["code"] == "resource_missing" and e["param"] == param
    assert charge(client, skh, pi["latest_charge"])["amount_refunded"] == 0


def test_other_merchants_refund_is_404(client, skh, other):
    re_ = ok(refund(client, skh, payment_intent=paid(client, skh)["id"]))
    assert client.get(f"/v1/refunds/{re_['id']}", headers=other).status_code == 404
    assert get(client, other, "/v1/refunds")["data"] == []


def test_publishable_key_is_403(client, skh, pkh):
    pi = paid(client, skh)
    assert refund(client, pkh, payment_intent=pi["id"]).status_code == 403
    assert charge(client, skh, pi["latest_charge"])["amount_refunded"] == 0
    re_ = ok(refund(client, skh, payment_intent=pi["id"]))
    assert client.get("/v1/refunds", headers=pkh).status_code == 403
    assert client.get(f"/v1/refunds/{re_['id']}", headers=pkh).status_code == 403


# --- list ----------------------------------------------------------------------------------


def test_list_newest_first_and_filters(client, skh):
    a = paid(client, skh)
    b = paid(client, skh)
    a1 = ok(refund(client, skh, charge=a["latest_charge"], amount="100"))
    b1 = ok(refund(client, skh, payment_intent=b["id"], amount="200"))
    a2 = ok(refund(client, skh, payment_intent=a["id"], amount="300"))

    def ids(query=""):
        return [x["id"] for x in get(client, skh, f"/v1/refunds{query}")["data"]]

    assert ids() == [a2["id"], b1["id"], a1["id"]]
    assert ids(f"?charge={a['latest_charge']}") == [a2["id"], a1["id"]]
    assert ids(f"?payment_intent={a['id']}") == [a2["id"], a1["id"]]
    assert ids(f"?charge={b['latest_charge']}") == [b1["id"]]
    assert ids(f"?payment_intent={b['id']}") == [b1["id"]]


# --- idempotency ---------------------------------------------------------------------------


def test_idempotent_refund_replays(client, skh):
    pi = paid(client, skh)
    h = {**skh, "Idempotency-Key": "refund-1"}
    first = refund(client, h, payment_intent=pi["id"], amount="400")
    second = refund(client, h, payment_intent=pi["id"], amount="400")
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert second.headers["idempotent-replayed"] == "true"
    assert len(get(client, skh, "/v1/refunds")["data"]) == 1
    assert len(refund_bts(client, skh)) == 1
    assert charge(client, skh, pi["latest_charge"])["amount_refunded"] == 400


# --- reconciliation ------------------------------------------------------------------------


def test_ledger_check_flags_amount_refunded_mismatch(client, db, skh):
    ch_id = ok(refund(client, skh, payment_intent=paid(client, skh)["id"], amount="300"))["charge"]
    assert ledger_check(db, own_txn=False) == []
    db.execute(
        text("UPDATE charges SET amount_refunded = amount_refunded - 1 WHERE id = :c"), {"c": ch_id}
    )
    assert ledger_check(db, own_txn=False) != []
    db.execute(
        text("UPDATE charges SET amount_refunded = amount_refunded + 1 WHERE id = :c"), {"c": ch_id}
    )
    assert ledger_check(db, own_txn=False) == []
