"""M9a: PaymentIntents are scoped to their merchant, including ids sent in the body."""

from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest

from app import seed
from app.ledger_check import ledger_check

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
EXP_YEAR = str(datetime.now(UTC).year + 5)


@pytest.fixture(autouse=True)
def ledger_clean(db):
    yield
    assert ledger_check(db, own_txn=False) == []


@pytest.fixture
def a(db):
    _, sk, pk = seed.seed(db, "pi-tenant-a@example.com")
    return {
        "sk": {"Authorization": f"Bearer {sk}", **FORM},
        "pk": {"Authorization": f"Bearer {pk}", **FORM},
    }


@pytest.fixture
def b(db):
    _, sk, pk = seed.seed(db, "pi-tenant-b@example.com")
    return {
        "sk": {"Authorization": f"Bearer {sk}", **FORM},
        "pk": {"Authorization": f"Bearer {pk}", **FORM},
    }


def err(r):
    return r.json()["error"]


def post(client, h, path, **data):
    return client.post(path, content=urlencode(data), headers=h)


def make_pm(client, pkh):
    body = (
        "type=card&card[number]=4242424242424242&card[exp_month]=12"
        f"&card[exp_year]={EXP_YEAR}&card[cvc]=123"
    )
    r = client.post("/v1/payment_methods", content=body, headers=pkh)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def make_cus(client, skh):
    r = client.post("/v1/customers", content="", headers=skh)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def create_r(client, h, **data):
    return post(client, h, "/v1/payment_intents", amount="1000", currency="usd", **data)


def create(client, h, **data):
    r = create_r(client, h, **data)
    assert r.status_code == 200, r.text
    return r.json()


def assert_missing(r, param=None):
    assert r.status_code == 404, r.text
    assert err(r)["code"] == "resource_missing"
    assert err(r).get("param") == param


# --- another merchant's PaymentIntent ------------------------------------------------------


def test_other_merchant_cannot_retrieve(client, a, b):
    pi = create(client, a["sk"])
    assert_missing(client.get(f"/v1/payment_intents/{pi['id']}", headers=b["sk"]))


def test_other_merchant_cannot_update(client, a, b):
    pi = create(client, a["sk"])
    assert_missing(post(client, b["sk"], f"/v1/payment_intents/{pi['id']}", amount="2000"))
    assert client.get(f"/v1/payment_intents/{pi['id']}", headers=a["sk"]).json() == pi


def test_other_merchant_cannot_cancel(client, a, b):
    pi = create(client, a["sk"])
    assert_missing(post(client, b["sk"], f"/v1/payment_intents/{pi['id']}/cancel"))
    got = client.get(f"/v1/payment_intents/{pi['id']}", headers=a["sk"]).json()
    assert got["status"] == "requires_payment_method"


def test_other_merchant_list_excludes_pi(client, a, b):
    pi = create(client, a["sk"])
    own = create(client, b["sk"])
    r = client.get("/v1/payment_intents", headers=b["sk"])
    assert r.status_code == 200, r.text
    assert [p["id"] for p in r.json()["data"]] == [own["id"]]
    assert pi["id"] not in [p["id"] for p in r.json()["data"]]


# --- ids in the request body ---------------------------------------------------------------


def test_create_with_other_merchants_customer_is_404(client, a, b):
    cus = make_cus(client, a["sk"])
    assert_missing(create_r(client, b["sk"], customer=cus), param="customer")


def test_create_with_other_merchants_payment_method_is_404(client, a, b):
    pm = make_pm(client, a["pk"])
    assert_missing(create_r(client, b["sk"], payment_method=pm), param="payment_method")


def test_create_with_unknown_customer_is_404(client, a):
    assert_missing(create_r(client, a["sk"], customer="cus_" + "0" * 24), param="customer")


def test_create_with_unknown_payment_method_is_404(client, a):
    r = create_r(client, a["sk"], payment_method="pm_doesnotexist")
    assert_missing(r, param="payment_method")


def test_update_with_other_merchants_customer_is_404(client, a, b):
    cus = make_cus(client, a["sk"])
    pi = create(client, b["sk"])
    r = post(client, b["sk"], f"/v1/payment_intents/{pi['id']}", customer=cus)
    assert_missing(r, param="customer")


def test_update_with_other_merchants_payment_method_is_404(client, a, b):
    pm = make_pm(client, a["pk"])
    pi = create(client, b["sk"])
    r = post(client, b["sk"], f"/v1/payment_intents/{pi['id']}", payment_method=pm)
    assert_missing(r, param="payment_method")
    got = client.get(f"/v1/payment_intents/{pi['id']}", headers=b["sk"]).json()
    assert got["status"] == "requires_payment_method" and got["payment_method"] is None


def test_create_with_deleted_customer_is_404(client, a):
    cus = make_cus(client, a["sk"])
    assert client.delete(f"/v1/customers/{cus}", headers=a["sk"]).status_code == 200
    assert_missing(create_r(client, a["sk"], customer=cus), param="customer")


def test_update_with_deleted_customer_is_404(client, a):
    cus = make_cus(client, a["sk"])
    pi = create(client, a["sk"])
    assert client.delete(f"/v1/customers/{cus}", headers=a["sk"]).status_code == 200
    r = post(client, a["sk"], f"/v1/payment_intents/{pi['id']}", customer=cus)
    assert_missing(r, param="customer")
