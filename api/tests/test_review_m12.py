"""Regression tests for the M12 expand review: no echo, tenancy inside expansions, odd
inputs, sub-route, and a bounded number of queries for list expansion."""

import pytest
from sqlalchemy import event, text

from app import seed
from app.ledger_check import ledger_check

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@pytest.fixture
def a(db):
    m, sk, _ = seed.seed(db, "x12a@example.com")
    return m, {"Authorization": f"Bearer {sk}"}


@pytest.fixture
def b(db):
    m, sk, _ = seed.seed(db, "x12b@example.com")
    return m, {"Authorization": f"Bearer {sk}"}


@pytest.fixture(autouse=True)
def _reconciles(db):
    yield
    assert ledger_check(db, own_txn=False) == []


def paid(client, h, extra=""):
    return client.post(
        "/v1/payment_intents",
        content="amount=1000&currency=usd&payment_method=pm_card_visa&confirm=true" + extra,
        headers={**h, **FORM},
    ).json()


def get(client, h, url):
    return client.get(url, headers=h)


def test_unknown_path_with_a_card_number_is_not_echoed(client, a):
    pi = paid(client, a[1])
    r = get(client, a[1], f"/v1/payment_intents/{pi['id']}?expand[]=4242424242424242")
    assert r.status_code == 400 and "4242" not in r.text


def test_list_hint_only_when_the_prefix_is_missing(client, a):
    r = get(client, a[1], "/v1/payment_intents?expand[]=data.nope")
    assert r.status_code == 400 and "prefix paths" not in r.json()["error"]["message"]


@pytest.mark.parametrize("query", ["expand=customer", "expand[]=", "expand[]=Customer"])
def test_odd_expand_inputs_are_400(client, a, query):
    pi = paid(client, a[1])
    r = get(client, a[1], f"/v1/payment_intents/{pi['id']}?{query}")
    assert r.status_code == 400 and r.json()["error"]["param"] == "expand"


def test_duplicate_paths_are_fine(client, a):
    pi = paid(client, a[1])
    r = get(
        client,
        a[1],
        f"/v1/payment_intents/{pi['id']}?expand[]=latest_charge&expand[]=latest_charge",
    )
    assert r.status_code == 200 and isinstance(r.json()["latest_charge"], dict)


def test_expansion_never_renders_another_merchants_object(client, db, a, b):
    theirs = client.post("/v1/customers", content="email=b@x.com", headers={**b[1], **FORM}).json()
    mine = client.post(
        "/v1/payment_intents", content="amount=1000&currency=usd", headers={**a[1], **FORM}
    ).json()
    # Simulate a corrupted cross-tenant reference (the API never allows one).
    db.execute(
        text("UPDATE payment_intents SET customer_id = :c WHERE id = :p"),
        {"c": theirs["id"], "p": mine["id"]},
    )
    r = get(client, a[1], f"/v1/payment_intents/{mine['id']}?expand[]=customer")
    assert r.status_code == 404 and "b@x.com" not in r.text
    assert r.json()["error"]["param"] == "expand"


def test_unconfirmed_pi_keeps_null_expansions(client, a):
    pi = client.post(
        "/v1/payment_intents", content="amount=1000&currency=usd", headers={**a[1], **FORM}
    ).json()
    r = get(
        client,
        a[1],
        f"/v1/payment_intents/{pi['id']}?expand[]=payment_method&expand[]=latest_charge",
    ).json()
    assert r["payment_method"] is None and r["latest_charge"] is None


def test_list_expands_payment_method_and_latest_charge(client, a):
    paid(client, a[1])
    item = get(
        client,
        a[1],
        "/v1/payment_intents?expand[]=data.payment_method&expand[]=data.latest_charge",
    ).json()["data"][0]
    assert item["payment_method"]["object"] == "payment_method"
    assert item["latest_charge"]["object"] == "charge"


def test_list_with_deleted_customer_gives_stub(client, a):
    cus = client.post("/v1/customers", headers=a[1]).json()["id"]
    paid(client, a[1], f"&customer={cus}")
    client.delete(f"/v1/customers/{cus}", headers=a[1])
    item = get(client, a[1], "/v1/payment_intents?expand[]=data.customer").json()["data"][0]
    assert item["customer"] == {"id": cus, "object": "customer", "deleted": True}


@pytest.mark.parametrize(
    "url",
    [
        "/v1/charges?expand[]=refunds",
        "/v1/charges/{ch}?expand[]=data.refunds",
        "/v1/charges/{ch}?expand[]=nope",
    ],
)
def test_charge_expand_errors(client, a, url):
    ch = paid(client, a[1])["latest_charge"]
    r = get(client, a[1], url.format(ch=ch))
    assert r.status_code == 400 and r.json()["error"]["param"] == "expand"


def test_post_still_rejects_expand(client, a):
    r = client.post(
        "/v1/payment_intents",
        content="amount=1000&currency=usd&expand[]=customer",
        headers={**a[1], **FORM},
    )
    assert r.json()["error"]["code"] == "parameter_unknown"


def test_charge_refunds_sub_route(client, a, b):
    ch = paid(client, a[1])["latest_charge"]
    client.post("/v1/refunds", content=f"charge={ch}&amount=100", headers={**a[1], **FORM})
    r = get(client, a[1], f"/v1/charges/{ch}/refunds").json()
    assert r["url"] == f"/v1/charges/{ch}/refunds" and len(r["data"]) == 1
    assert get(client, b[1], f"/v1/charges/{ch}/refunds").status_code == 404


def test_list_expansion_uses_a_bounded_number_of_queries(client, db, a):
    for _ in range(8):
        paid(client, a[1])
    statements = []

    def count(*args, **kwargs):
        statements.append(1)

    engine = db.get_bind().engine
    event.listen(engine, "before_cursor_execute", count)
    try:
        r = get(
            client,
            a[1],
            "/v1/payment_intents?limit=20&expand[]=data.customer"
            "&expand[]=data.payment_method&expand[]=data.latest_charge",
        )
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert r.status_code == 200 and len(r.json()["data"]) == 8
    assert len(statements) <= 12, len(statements)
