"""M9a: PaymentIntents without confirm: create, update, cancel, retrieve, list."""

import re
from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest

from app import seed
from app.ledger_check import ledger_check

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
PI_ID = re.compile(r"^pi_[0-9A-Za-z]+$")
KEYS = {
    "id",
    "object",
    "amount",
    "amount_capturable",
    "amount_received",
    "currency",
    "status",
    "capture_method",
    "customer",
    "payment_method",
    "latest_charge",
    "client_secret",
    "last_payment_error",
    "next_action",
    "return_url",
    "cancellation_reason",
    "metadata",
    "created",
    "livemode",
}
EXP_YEAR = str(datetime.now(UTC).year + 5)


@pytest.fixture(autouse=True)
def ledger_clean(db):
    yield
    assert ledger_check(db, own_txn=False) == []


@pytest.fixture
def keys(db):
    return seed.seed(db, "pi-a@example.com")


@pytest.fixture
def skh(keys):
    return {"Authorization": f"Bearer {keys[1]}", **FORM}


@pytest.fixture
def pkh(keys):
    return {"Authorization": f"Bearer {keys[2]}", **FORM}


def err(r):
    return r.json()["error"]


def make_pm(client, pkh):
    body = (
        "type=card&card[number]=4242424242424242&card[exp_month]=12"
        f"&card[exp_year]={EXP_YEAR}&card[cvc]=123"
    )
    r = client.post("/v1/payment_methods", content=body, headers=pkh)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def make_cus(client, skh, **data):
    r = client.post("/v1/customers", content=urlencode(data), headers=skh)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def post(client, h, path, **data):
    return client.post(path, content=urlencode(data), headers=h)


def create_r(client, h, **data):
    data = {"amount": "1000", "currency": "usd", **data}
    return post(client, h, "/v1/payment_intents", **data)


def create(client, h, **data):
    r = create_r(client, h, **data)
    assert r.status_code == 200, r.text
    return r.json()


def update(client, h, pid, **data):
    return post(client, h, f"/v1/payment_intents/{pid}", **data)


def cancel(client, h, pid, **data):
    return post(client, h, f"/v1/payment_intents/{pid}/cancel", **data)


def events(client, h, type):
    r = client.get("/v1/events", params={"type": type}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def assert_400(r, param=None, code=None):
    assert r.status_code == 400, r.text
    e = err(r)
    assert e["type"] == "invalid_request_error"
    if param is not None:
        assert e["param"] == param
    if code is not None:
        assert e["code"] == code


# --- create --------------------------------------------------------------------------------


def test_create_without_payment_method(client, skh):
    pi = create(client, skh)
    assert set(pi) == KEYS
    assert PI_ID.match(pi["id"])
    assert pi["object"] == "payment_intent"
    assert pi["status"] == "requires_payment_method"
    assert pi["amount"] == 1000 and pi["currency"] == "usd"
    assert pi["amount_capturable"] == 0 and pi["amount_received"] == 0
    assert pi["capture_method"] == "automatic_async"
    assert pi["customer"] is None and pi["payment_method"] is None
    assert pi["latest_charge"] is None
    assert pi["last_payment_error"] is None and pi["next_action"] is None
    assert pi["cancellation_reason"] is None
    assert pi["metadata"] == {}
    assert isinstance(pi["created"], int) and not isinstance(pi["created"], bool)
    assert pi["livemode"] is False
    assert re.fullmatch(re.escape(pi["id"]) + r"_secret_[0-9A-Za-z]{24}", pi["client_secret"])


def test_create_with_payment_method_requires_confirmation(client, skh, pkh):
    pm = make_pm(client, pkh)
    pi = create(client, skh, payment_method=pm)
    assert pi["status"] == "requires_confirmation"
    assert pi["payment_method"] == pm


def test_create_with_customer(client, skh):
    cus = make_cus(client, skh)
    assert create(client, skh, customer=cus)["customer"] == cus


def test_create_with_manual_capture(client, skh):
    assert create(client, skh, capture_method="manual")["capture_method"] == "manual"


def test_create_with_metadata_and_https_return_url(client, skh):
    pi = create(client, skh, return_url="https://example.com/done", **{"metadata[k]": "v"})
    assert pi["return_url"] == "https://example.com/done"
    assert pi["metadata"] == {"k": "v"}


@pytest.mark.parametrize("amount", ["49", "100000000", "0", "-1", "abc", "10.5"])
def test_create_amount_out_of_range_or_invalid_is_400(client, skh, amount):
    assert_400(create_r(client, skh, amount=amount), param="amount")


@pytest.mark.parametrize("amount", [50, 99999999])
def test_create_amount_bounds_inclusive(client, skh, amount):
    assert create(client, skh, amount=str(amount))["amount"] == amount


def test_create_non_usd_currency_is_400(client, skh):
    assert_400(create_r(client, skh, currency="eur"), param="currency")


def test_create_bad_capture_method_is_400(client, skh):
    assert_400(create_r(client, skh, capture_method="later"))


def test_create_unknown_param_is_400(client, skh):
    assert_400(create_r(client, skh, foo="1"), code="parameter_unknown")


def test_create_javascript_return_url_is_url_invalid(client, skh):
    assert_400(create_r(client, skh, return_url="javascript:alert(1)"), code="url_invalid")


def test_create_emits_payment_intent_created(client, skh):
    pi = create(client, skh)
    (e,) = events(client, skh, "payment_intent.created")
    assert e["data"]["object"] == pi


def test_failed_create_emits_nothing(client, skh):
    create_r(client, skh, amount="49")
    assert events(client, skh, "payment_intent.created") == []


# --- update --------------------------------------------------------------------------------


def test_update_amount_and_metadata_merges(client, skh):
    pi = create(client, skh, **{"metadata[a]": "1"})
    r = update(client, skh, pi["id"], amount="2000", **{"metadata[b]": "2"})
    assert r.status_code == 200, r.text
    assert r.json()["amount"] == 2000
    assert r.json()["metadata"] == {"a": "1", "b": "2"}


def test_update_amount_out_of_range_is_400(client, skh):
    pi = create(client, skh)
    assert_400(update(client, skh, pi["id"], amount="49"), param="amount")


def test_update_set_and_clear_payment_method_moves_status(client, skh, pkh):
    pi = create(client, skh)
    pm = make_pm(client, pkh)
    r = update(client, skh, pi["id"], payment_method=pm)
    assert r.status_code == 200, r.text
    assert r.json()["payment_method"] == pm
    assert r.json()["status"] == "requires_confirmation"
    r = update(client, skh, pi["id"], payment_method="")
    assert r.status_code == 200, r.text
    assert r.json()["payment_method"] is None
    assert r.json()["status"] == "requires_payment_method"


def test_update_empty_customer_clears_it(client, skh):
    cus = make_cus(client, skh)
    pi = create(client, skh, customer=cus)
    r = update(client, skh, pi["id"], customer="")
    assert r.status_code == 200, r.text
    assert r.json()["customer"] is None


def test_update_unknown_param_is_400(client, skh):
    pi = create(client, skh)
    assert_400(update(client, skh, pi["id"], foo="1"), code="parameter_unknown")


def test_update_canceled_is_unexpected_state(client, skh):
    pi = create(client, skh)
    assert cancel(client, skh, pi["id"]).status_code == 200
    r = update(client, skh, pi["id"], amount="2000")
    assert_400(r, code="payment_intent_unexpected_state")


def test_update_unknown_id_is_404(client, skh):
    r = update(client, skh, "pi_doesnotexist", amount="2000")
    assert r.status_code == 404 and err(r)["code"] == "resource_missing"


# --- cancel --------------------------------------------------------------------------------


def test_cancel_from_requires_payment_method_with_reason(client, skh):
    pi = create(client, skh)
    r = cancel(client, skh, pi["id"], cancellation_reason="duplicate")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == "canceled"
    assert got["cancellation_reason"] == "duplicate"
    assert got["amount_capturable"] == 0
    (e,) = events(client, skh, "payment_intent.canceled")
    assert e["data"]["object"] == got


def test_cancel_from_requires_confirmation(client, skh, pkh):
    pi = create(client, skh, payment_method=make_pm(client, pkh))
    r = cancel(client, skh, pi["id"], cancellation_reason="requested_by_customer")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "canceled"
    assert r.json()["cancellation_reason"] == "requested_by_customer"
    assert r.json()["amount_capturable"] == 0
    (e,) = events(client, skh, "payment_intent.canceled")
    assert e["data"]["object"]["id"] == pi["id"]


@pytest.mark.parametrize(
    "reason", ["duplicate", "fraudulent", "requested_by_customer", "abandoned"]
)
def test_cancel_accepts_each_reason(client, skh, reason):
    pi = create(client, skh)
    r = cancel(client, skh, pi["id"], cancellation_reason=reason)
    assert r.status_code == 200, r.text
    assert r.json()["cancellation_reason"] == reason


def test_cancel_twice_is_unexpected_state(client, skh):
    pi = create(client, skh)
    assert cancel(client, skh, pi["id"]).status_code == 200
    assert_400(cancel(client, skh, pi["id"]), code="payment_intent_unexpected_state")
    assert len(events(client, skh, "payment_intent.canceled")) == 1


def test_cancel_bad_reason_is_400(client, skh):
    pi = create(client, skh)
    assert_400(cancel(client, skh, pi["id"], cancellation_reason="bored"))
    got = client.get(f"/v1/payment_intents/{pi['id']}", headers=skh).json()
    assert got["status"] == "requires_payment_method"


def test_cancel_unknown_id_is_404(client, skh):
    r = cancel(client, skh, "pi_doesnotexist")
    assert r.status_code == 404 and err(r)["code"] == "resource_missing"


# --- retrieve and list ---------------------------------------------------------------------


def test_retrieve_equals_create(client, skh):
    pi = create(client, skh, **{"metadata[k]": "v"})
    r = client.get(f"/v1/payment_intents/{pi['id']}", headers=skh)
    assert r.status_code == 200, r.text
    assert r.json() == pi


def test_retrieve_unknown_is_404(client, skh):
    r = client.get("/v1/payment_intents/pi_doesnotexist", headers=skh)
    assert r.status_code == 404
    assert err(r)["code"] == "resource_missing"


def test_list_newest_first(client, skh):
    ids = [create(client, skh)["id"] for _ in range(3)]
    r = client.get("/v1/payment_intents", headers=skh)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "list"
    assert [p["id"] for p in body["data"]] == ids[::-1]


def test_list_customer_filter(client, skh):
    cus = make_cus(client, skh)
    mine = create(client, skh, customer=cus)["id"]
    create(client, skh)
    create(client, skh, customer=make_cus(client, skh))
    r = client.get("/v1/payment_intents", params={"customer": cus}, headers=skh)
    assert r.status_code == 200, r.text
    assert [p["id"] for p in r.json()["data"]] == [mine]


# --- publishable key -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/v1/payment_intents"),
        ("post", "/v1/payment_intents/{id}"),
        ("post", "/v1/payment_intents/{id}/cancel"),
        ("get", "/v1/payment_intents"),
        ("get", "/v1/payment_intents/{id}"),
    ],
)
def test_publishable_key_is_403(client, skh, pkh, method, path):
    pi = create(client, skh)
    url = path.format(id=pi["id"])
    if method == "post":
        r = client.post(
            url, content="amount=1000&currency=usd" if "{" not in path else "", headers=pkh
        )
    else:
        r = client.get(url, headers=pkh)
    assert r.status_code == 403, r.text
    assert err(r)["code"] == "permission_denied"
