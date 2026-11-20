"""M9b: confirming PaymentIntents: charges, ledger posting, declines, 3DS requires_action."""

import re
from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest
from sqlalchemy import text

from app import ledger, seed
from app.ledger_check import ledger_check

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
EXP_YEAR = str(datetime.now(UTC).year + 5)
CHARGE_KEYS = {
    "id",
    "object",
    "amount",
    "amount_captured",
    "amount_refunded",
    "captured",
    "refunded",
    "status",
    "failure_code",
    "failure_message",
    "payment_intent",
    "payment_method",
    "payment_method_details",
    "balance_transaction",
    "currency",
    "created",
    "livemode",
}
RETURN_URL = "https://example.com/done"


@pytest.fixture(autouse=True)
def ledger_clean(db):
    yield
    assert ledger_check(db, own_txn=False) == []


@pytest.fixture
def keys(db):
    return seed.seed(db, "confirm-a@example.com")


@pytest.fixture
def skh(keys):
    return {"Authorization": f"Bearer {keys[1]}", **FORM}


@pytest.fixture
def pkh(keys):
    return {"Authorization": f"Bearer {keys[2]}", **FORM}


def err(r):
    return r.json()["error"]


def post(client, h, path, **data):
    return client.post(path, content=urlencode(data), headers=h)


def create(client, h, **data):
    data = {"amount": "1000", "currency": "usd", **data}
    r = post(client, h, "/v1/payment_intents", **data)
    assert r.status_code == 200, r.text
    return r.json()


def confirm(client, h, pid, **data):
    return post(client, h, f"/v1/payment_intents/{pid}/confirm", **data)


def retrieve(client, h, pid):
    r = client.get(f"/v1/payment_intents/{pid}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def tokenize(client, pkh, number):
    body = (
        f"type=card&card[number]={number}&card[exp_month]=12"
        f"&card[exp_year]={EXP_YEAR}&card[cvc]=123"
    )
    r = client.post("/v1/payment_methods", content=body, headers=pkh)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def charges_for(client, h, pid):
    r = client.get("/v1/charges", params={"payment_intent": pid}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def event_types(client, h):
    """Charge and PaymentIntent event types, oldest first."""
    r = client.get("/v1/events", params={"limit": 100}, headers=h)
    assert r.status_code == 200, r.text
    types = [e["type"] for e in r.json()["data"]][::-1]
    return [t for t in types if t.startswith(("charge.", "payment_intent."))]


def events(client, h, type):
    r = client.get("/v1/events", params={"type": type}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def assert_400(r, code, param=None):
    assert r.status_code == 400, r.text
    e = err(r)
    assert e["type"] == "invalid_request_error"
    assert e["code"] == code
    if param is not None:
        assert e["param"] == param


def bt_count(db, merchant_id):
    return db.scalar(
        text("SELECT count(*) FROM balance_transactions WHERE merchant_id = :m"),
        {"m": merchant_id},
    )


def pending(db, merchant_id):
    return ledger.balance(db, merchant_id)["pending"]


# --- success -------------------------------------------------------------------------------


@pytest.mark.parametrize("capture_method", [None, "automatic"])
def test_automatic_capture_success(client, db, keys, skh, capture_method):
    extra = {"capture_method": capture_method} if capture_method else {}
    pi = create(client, skh, **extra)
    r = confirm(client, skh, pi["id"], payment_method="pm_card_visa")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == "succeeded"
    assert got["amount_received"] == 1000
    assert got["amount_capturable"] == 0
    assert re.fullmatch(r"pm_[0-9A-Za-z]+", got["payment_method"])
    assert got["payment_method"] != "pm_card_visa"
    assert re.fullmatch(r"ch_[0-9A-Za-z]+", got["latest_charge"])
    assert got["last_payment_error"] is None and got["next_action"] is None
    assert retrieve(client, skh, pi["id"]) == got

    (ch,) = charges_for(client, skh, pi["id"])
    assert set(ch) == CHARGE_KEYS
    assert ch["id"] == got["latest_charge"]
    assert ch["object"] == "charge"
    assert ch["amount"] == 1000 and ch["currency"] == "usd"
    assert ch["status"] == "succeeded"
    assert ch["captured"] is True and ch["amount_captured"] == 1000
    assert ch["refunded"] is False and ch["amount_refunded"] == 0
    assert ch["failure_code"] is None and ch["failure_message"] is None
    assert ch["payment_intent"] == pi["id"]
    assert ch["payment_method"] == got["payment_method"]
    card = ch["payment_method_details"]["card"]
    assert set(ch["payment_method_details"]) == {"card"}
    assert set(card) == {"brand", "last4", "exp_month", "exp_year"}
    assert card["brand"] == "visa" and card["last4"] == "4242"
    assert re.fullmatch(r"txn_[0-9A-Za-z]+", ch["balance_transaction"])
    assert ch["livemode"] is False
    assert isinstance(ch["created"], int) and not isinstance(ch["created"], bool)

    assert pending(db, keys[0].id) == [{"amount": 1000 - ledger.fee(1000), "currency": "usd"}]
    assert event_types(client, skh) == [
        "payment_intent.created",
        "charge.succeeded",
        "payment_intent.succeeded",
    ]
    (e,) = events(client, skh, "payment_intent.succeeded")
    assert e["data"]["object"] == got


def test_manual_capture_success(client, db, keys, skh):
    pi = create(client, skh, capture_method="manual")
    r = confirm(client, skh, pi["id"], payment_method="pm_card_visa")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == "requires_capture"
    assert got["amount_capturable"] == 1000
    assert got["amount_received"] == 0
    (ch,) = charges_for(client, skh, pi["id"])
    assert ch["id"] == got["latest_charge"]
    assert ch["status"] == "succeeded"
    assert ch["captured"] is False and ch["amount_captured"] == 0
    assert ch["balance_transaction"] is None
    assert bt_count(db, keys[0].id) == 0
    assert pending(db, keys[0].id) == [{"amount": 0, "currency": "usd"}]
    assert event_types(client, skh) == [
        "payment_intent.created",
        "charge.succeeded",
        "payment_intent.amount_capturable_updated",
    ]


# --- declines ------------------------------------------------------------------------------

DECLINES = [
    ("pm_card_visa_chargeDeclined", "0002", "card_declined", "generic_decline"),
    ("pm_card_visa_chargeDeclinedInsufficientFunds", "9995", "card_declined", "insufficient_funds"),
    ("4000000000000069", "0069", "expired_card", None),
    ("4000000000000127", "0127", "incorrect_cvc", None),
    ("4000000000000119", "0119", "processing_error", None),
]


def pm_param(client, pkh, card):
    """A `pm_card_*` test id is passed as is; a card number is tokenized first."""
    return card if card.startswith("pm_") else tokenize(client, pkh, card)


@pytest.mark.parametrize(("card", "last4", "code", "decline_code"), DECLINES)
def test_decline(client, db, keys, skh, pkh, card, last4, code, decline_code):
    pm = pm_param(client, pkh, card)
    pi = create(client, skh)
    r = confirm(client, skh, pi["id"], payment_method=pm)
    assert r.status_code == 402, r.text
    e = err(r)
    assert e["type"] == "card_error"
    assert e["code"] == code
    if decline_code:
        assert e["decline_code"] == decline_code
    else:
        assert "decline_code" not in e
    assert isinstance(e["message"], str) and e["message"]

    got = retrieve(client, skh, pi["id"])
    assert e["payment_intent"] == got
    assert got["status"] == "requires_payment_method"
    assert got["payment_method"] is None
    lpe = got["last_payment_error"]
    assert lpe["type"] == "card_error"
    assert lpe["code"] == code
    if decline_code:
        assert lpe["decline_code"] == decline_code
    else:
        assert "decline_code" not in lpe
    assert isinstance(lpe["message"], str) and lpe["message"]
    assert re.fullmatch(r"pm_[0-9A-Za-z]+", lpe["payment_method"]["id"])
    assert lpe["payment_method"]["card"]["last4"] == last4
    if not card.startswith("pm_card"):
        assert lpe["payment_method"]["id"] == pm

    (ch,) = charges_for(client, skh, pi["id"])
    assert ch["status"] == "failed"
    assert ch["failure_code"] == code
    assert isinstance(ch["failure_message"], str) and ch["failure_message"]
    assert ch["captured"] is False and ch["amount_captured"] == 0
    assert ch["balance_transaction"] is None
    assert ch["payment_method"] == lpe["payment_method"]["id"]
    assert bt_count(db, keys[0].id) == 0
    assert event_types(client, skh) == [
        "payment_intent.created",
        "charge.failed",
        "payment_intent.payment_failed",
    ]


def test_declined_confirm_replays_with_same_idempotency_key(client, skh):
    pi = create(client, skh)
    h = {**skh, "Idempotency-Key": "confirm-decline-1"}
    first = confirm(client, h, pi["id"], payment_method="pm_card_visa_chargeDeclined")
    second = confirm(client, h, pi["id"], payment_method="pm_card_visa_chargeDeclined")
    assert first.status_code == 402, first.text
    assert second.status_code == 402, second.text
    assert second.json() == first.json()
    assert second.headers["idempotent-replayed"] == "true"
    assert len(charges_for(client, skh, pi["id"])) == 1
    assert len(events(client, skh, "charge.failed")) == 1


def test_retry_after_decline_succeeds(client, skh):
    pi = create(client, skh)
    r = confirm(client, skh, pi["id"], payment_method="pm_card_visa_chargeDeclined")
    assert r.status_code == 402, r.text
    (failed,) = charges_for(client, skh, pi["id"])
    r = confirm(client, skh, pi["id"], payment_method="pm_card_visa")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == "succeeded"
    assert got["last_payment_error"] is None
    chs = charges_for(client, skh, pi["id"])
    assert len(chs) == 2
    assert got["latest_charge"] == chs[0]["id"] != failed["id"]
    assert chs[0]["status"] == "succeeded"


# --- 3DS -----------------------------------------------------------------------------------

THREE_DS = ["pm_card_authenticationRequired", "4000002760003184"]


@pytest.mark.parametrize("card", THREE_DS)
def test_3ds_without_return_url_is_400(client, skh, pkh, card):
    pi = create(client, skh)
    r = confirm(client, skh, pi["id"], payment_method=pm_param(client, pkh, card))
    assert_400(r, "parameter_missing", param="return_url")
    assert charges_for(client, skh, pi["id"]) == []


@pytest.mark.parametrize("return_url_on", ["confirm", "payment_intent"])
@pytest.mark.parametrize("card", THREE_DS)
def test_3ds_requires_action(client, skh, pkh, card, return_url_on):
    pm = pm_param(client, pkh, card)
    if return_url_on == "payment_intent":
        pi = create(client, skh, return_url=RETURN_URL)
        r = confirm(client, skh, pi["id"], payment_method=pm)
    else:
        pi = create(client, skh)
        r = confirm(client, skh, pi["id"], payment_method=pm, return_url=RETURN_URL)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == "requires_action"
    na = got["next_action"]
    assert set(na) == {"type", "redirect_to_url"}
    assert na["type"] == "redirect_to_url"
    assert set(na["redirect_to_url"]) == {"url", "return_url"}
    assert na["redirect_to_url"]["return_url"] == RETURN_URL
    url = na["redirect_to_url"]["url"]
    assert url.startswith("http")
    assert f"/checkout/3ds/{pi['id']}" in url
    assert pi["client_secret"] in url
    assert got["latest_charge"] is None
    assert charges_for(client, skh, pi["id"]) == []
    (e,) = events(client, skh, "payment_intent.requires_action")
    assert e["data"]["object"] == got


# --- invalid states and params -------------------------------------------------------------


def test_confirm_without_any_payment_method_is_400(client, skh):
    pi = create(client, skh)
    assert_400(confirm(client, skh, pi["id"]), "parameter_missing", param="payment_method")


def test_confirm_canceled_is_unexpected_state(client, skh):
    pi = create(client, skh)
    assert post(client, skh, f"/v1/payment_intents/{pi['id']}/cancel").status_code == 200
    r = confirm(client, skh, pi["id"], payment_method="pm_card_visa")
    assert_400(r, "payment_intent_unexpected_state")


def test_confirm_twice_after_success_is_unexpected_state(client, skh):
    pi = create(client, skh)
    assert confirm(client, skh, pi["id"], payment_method="pm_card_visa").status_code == 200
    assert_400(confirm(client, skh, pi["id"]), "payment_intent_unexpected_state")
    assert len(charges_for(client, skh, pi["id"])) == 1


# --- create(confirm=true) and update --------------------------------------------------------


def test_create_with_confirm_true(client, db, keys, skh):
    pi = create(client, skh, payment_method="pm_card_visa", confirm="true")
    assert pi["status"] == "succeeded"
    assert pi["amount_received"] == 1000
    (ch,) = charges_for(client, skh, pi["id"])
    assert pi["latest_charge"] == ch["id"]
    assert ch["captured"] is True
    assert pending(db, keys[0].id) == [{"amount": 1000 - ledger.fee(1000), "currency": "usd"}]
    assert event_types(client, skh) == [
        "payment_intent.created",
        "charge.succeeded",
        "payment_intent.succeeded",
    ]


def test_update_clears_last_payment_error(client, skh):
    pi = create(client, skh)
    r = confirm(client, skh, pi["id"], payment_method="pm_card_visa_chargeDeclined")
    assert r.status_code == 402, r.text
    assert retrieve(client, skh, pi["id"])["last_payment_error"] is not None
    r = post(client, skh, f"/v1/payment_intents/{pi['id']}", **{"metadata[a]": "1"})
    assert r.status_code == 200, r.text
    assert r.json()["last_payment_error"] is None
