"""M12: one-level expand[] on GET endpoints."""

from urllib.parse import urlencode

import pytest

from app import seed

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@pytest.fixture
def h(db):
    _, sk, _ = seed.seed(db, "expand-a@example.com")
    return {"Authorization": f"Bearer {sk}", **FORM}


@pytest.fixture
def other(db):
    _, sk, _ = seed.seed(db, "expand-b@example.com")
    return {"Authorization": f"Bearer {sk}", **FORM}


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def get(client, h, path):
    return ok(client.get(path, headers=h))


def customer(client, h):
    return ok(client.post("/v1/customers", content="email=a@x.com", headers=h))


def paid(client, h, cus=None):
    data = {"amount": 1000, "currency": "usd", "payment_method": "pm_card_visa", "confirm": "true"}
    if cus:
        data["customer"] = cus
    pi = ok(client.post("/v1/payment_intents", content=urlencode(data), headers=h))
    assert pi["status"] == "succeeded"
    return pi


def paid_with_customer(client, h):
    cus = customer(client, h)
    return paid(client, h, cus["id"]), cus


def refund(client, h, charge, amount):
    return ok(client.post("/v1/refunds", content=f"charge={charge}&amount={amount}", headers=h))


def assert_expand_error(r):
    assert r.status_code == 400, r.text
    e = r.json()["error"]
    assert e["param"] == "expand"
    # Today expand is an unknown parameter; once supported, a bad path is a different error.
    assert e.get("code") != "parameter_unknown"


# --- PaymentIntent retrieve ---------------------------------------------------------------


def test_expand_customer_equals_customer_retrieve(client, h):
    pi, cus = paid_with_customer(client, h)
    got = get(client, h, f"/v1/payment_intents/{pi['id']}?expand[]=customer")
    assert got["customer"] == get(client, h, f"/v1/customers/{cus['id']}")
    assert got["customer"]["email"] == "a@x.com"


def test_expand_deleted_customer_gives_stub(client, h):
    pi, cus = paid_with_customer(client, h)
    ok(client.delete(f"/v1/customers/{cus['id']}", headers=h))
    got = get(client, h, f"/v1/payment_intents/{pi['id']}?expand[]=customer")
    assert got["customer"] == {"id": cus["id"], "object": "customer", "deleted": True}


def test_expand_null_customer_stays_null(client, h):
    pi = paid(client, h)
    got = get(client, h, f"/v1/payment_intents/{pi['id']}?expand[]=customer")
    assert got["customer"] is None


def test_expand_payment_method_equals_payment_method_retrieve(client, h):
    pi = paid(client, h)
    got = get(client, h, f"/v1/payment_intents/{pi['id']}?expand[]=payment_method")
    assert got["payment_method"] == get(client, h, f"/v1/payment_methods/{pi['payment_method']}")


def test_expand_latest_charge_equals_charge_retrieve(client, h):
    pi = paid(client, h)
    got = get(client, h, f"/v1/payment_intents/{pi['id']}?expand[]=latest_charge")
    assert got["latest_charge"] == get(client, h, f"/v1/charges/{pi['latest_charge']}")


def test_expand_all_three_paths_together(client, h):
    pi, cus = paid_with_customer(client, h)
    got = ok(
        client.get(
            f"/v1/payment_intents/{pi['id']}",
            params=[
                ("expand[]", "customer"),
                ("expand[]", "payment_method"),
                ("expand[]", "latest_charge"),
            ],
            headers=h,
        )
    )
    assert got["customer"] == get(client, h, f"/v1/customers/{cus['id']}")
    assert got["payment_method"] == get(client, h, f"/v1/payment_methods/{pi['payment_method']}")
    assert got["latest_charge"] == get(client, h, f"/v1/charges/{pi['latest_charge']}")
    rest = {
        k: v for k, v in got.items() if k not in ("customer", "payment_method", "latest_charge")
    }
    assert rest == {k: v for k, v in pi.items() if k in rest}


def test_without_expand_ids_stay_strings(client, h):
    pi, cus = paid_with_customer(client, h)
    got = get(client, h, f"/v1/payment_intents/{pi['id']}")
    assert got["customer"] == cus["id"]
    assert isinstance(got["payment_method"], str)
    assert isinstance(got["latest_charge"], str)
    assert got == pi


def test_expand_indexed_form(client, h):
    pi, cus = paid_with_customer(client, h)
    got = get(client, h, f"/v1/payment_intents/{pi['id']}?expand[0]=customer")
    assert got["customer"] == get(client, h, f"/v1/customers/{cus['id']}")


# --- errors -------------------------------------------------------------------------------


def test_expand_unknown_path_is_400(client, h):
    pi = paid(client, h)
    assert_expand_error(client.get(f"/v1/payment_intents/{pi['id']}?expand[]=nope", headers=h))


def test_expand_nested_path_is_400(client, h):
    pi, _ = paid_with_customer(client, h)
    r = client.get(f"/v1/payment_intents/{pi['id']}?expand[]=customer.foo", headers=h)
    assert_expand_error(r)


def test_expand_bare_path_on_list_is_400(client, h):
    paid_with_customer(client, h)
    assert_expand_error(client.get("/v1/payment_intents?expand[]=customer", headers=h))


def test_expand_data_path_on_retrieve_is_400(client, h):
    pi, _ = paid_with_customer(client, h)
    r = client.get(f"/v1/payment_intents/{pi['id']}?expand[]=data.customer", headers=h)
    assert_expand_error(r)


# --- lists --------------------------------------------------------------------------------


def test_list_data_customer_expands_every_item(client, h):
    pi1, cus1 = paid_with_customer(client, h)
    pi2, cus2 = paid_with_customer(client, h)
    page = get(client, h, "/v1/payment_intents?expand[]=data.customer")
    by_id = {pi["id"]: pi for pi in page["data"]}
    assert set(by_id) == {pi1["id"], pi2["id"]}
    assert by_id[pi1["id"]]["customer"] == get(client, h, f"/v1/customers/{cus1['id']}")
    assert by_id[pi2["id"]]["customer"] == get(client, h, f"/v1/customers/{cus2['id']}")


# --- Charge refunds -----------------------------------------------------------------------


def test_charge_expand_refunds_newest_first(client, h):
    ch = paid(client, h)["latest_charge"]
    r1 = refund(client, h, ch, 300)
    r2 = refund(client, h, ch, 200)
    got = get(client, h, f"/v1/charges/{ch}?expand[]=refunds")
    assert got["refunds"] == {
        "object": "list",
        "data": [
            get(client, h, f"/v1/refunds/{r2['id']}"),
            get(client, h, f"/v1/refunds/{r1['id']}"),
        ],
        "has_more": False,
        "url": f"/v1/charges/{ch}/refunds",
    }
    plain = get(client, h, f"/v1/charges/{ch}")
    assert {k: v for k, v in got.items() if k != "refunds"} == plain


def test_charge_without_expand_has_no_refunds_key(client, h):
    ch = paid(client, h)["latest_charge"]
    refund(client, h, ch, 300)
    assert "refunds" not in get(client, h, f"/v1/charges/{ch}")
    assert "refunds" not in get(client, h, "/v1/charges")["data"][0]


def test_charge_list_data_refunds(client, h):
    ch1 = paid(client, h)["latest_charge"]
    ch2 = paid(client, h)["latest_charge"]
    rf = refund(client, h, ch1, 300)
    page = get(client, h, "/v1/charges?expand[]=data.refunds")
    by_id = {c["id"]: c for c in page["data"]}
    assert set(by_id) == {ch1, ch2}
    assert by_id[ch1]["refunds"]["data"] == [get(client, h, f"/v1/refunds/{rf['id']}")]
    assert by_id[ch1]["refunds"]["url"] == f"/v1/charges/{ch1}/refunds"
    assert by_id[ch2]["refunds"] == {
        "object": "list",
        "data": [],
        "has_more": False,
        "url": f"/v1/charges/{ch2}/refunds",
    }


# --- tenancy ------------------------------------------------------------------------------


def test_other_merchant_expand_is_404(client, h, other):
    pi, _ = paid_with_customer(client, h)
    r = client.get(f"/v1/payment_intents/{pi['id']}?expand[]=customer", headers=other)
    assert r.status_code == 404, r.text
