import pytest

from app import seed

BT_KEYS = {
    "id",
    "object",
    "amount",
    "fee",
    "net",
    "currency",
    "type",
    "status",
    "available_on",
    "source",
    "created",
    "livemode",
}


@pytest.fixture
def keys(db):
    return seed.seed(db, "a@example.com")


@pytest.fixture
def mid(keys):
    return keys[0].id


@pytest.fixture
def h(keys):
    return {"Authorization": f"Bearer {keys[1]}"}


@pytest.fixture
def ledger():
    # Imported lazily so the route tests fail on 404s, not on collection, before M6 exists.
    from app import ledger

    return ledger


def err(r):
    return r.json()["error"]


def ids(r):
    return [b["id"] for b in r.json()["data"]]


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


# --- balance -------------------------------------------------------------------------------


def test_balance_for_fresh_merchant_is_zero(client, h):
    r = client.get("/v1/balance", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "object": "balance",
        "available": [{"amount": 0, "currency": "usd"}],
        "pending": [{"amount": 0, "currency": "usd"}],
        "livemode": False,
    }


def test_balance_shows_pending_after_capture(client, h, db, mid, ledger):
    ledger.post_capture(db, mid, "ch_1", 1000)
    db.flush()
    body = client.get("/v1/balance", headers=h).json()
    assert body["pending"] == [{"amount": 941, "currency": "usd"}]
    assert body["available"] == [{"amount": 0, "currency": "usd"}]


# --- balance_transactions ------------------------------------------------------------------


def test_list_is_newest_first_with_exact_keys(client, h, db, mid, ledger):
    a = ledger.post_capture(db, mid, "ch_1", 1000)
    b = ledger.post_capture(db, mid, "ch_2", 2000)
    db.flush()
    r = client.get("/v1/balance_transactions", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "list"
    assert body["has_more"] is False
    assert ids(r) == [b.id, a.id]
    item = body["data"][1]
    assert set(item) == BT_KEYS
    assert item["object"] == "balance_transaction"
    assert (item["amount"], item["fee"], item["net"]) == (1000, 59, 941)
    assert (item["currency"], item["type"], item["status"]) == ("usd", "charge", "pending")
    assert item["source"] == "ch_1"
    assert is_int(item["available_on"]) and is_int(item["created"])
    assert item["available_on"] == int(a.available_on.timestamp())


def test_list_filters_by_type(client, h, db, mid, ledger):
    ledger.release(db, ledger.post_capture(db, mid, "ch_1", 1000))
    refund = ledger.post_refund(db, mid, "re_1", 400)
    db.flush()
    r = client.get("/v1/balance_transactions", params={"type": "refund"}, headers=h)
    assert r.status_code == 200, r.text
    assert ids(r) == [refund.id]
    assert r.json()["data"][0]["amount"] == -400


def test_list_paginates_with_limit_and_starting_after(client, h, db, mid, ledger):
    bts = [ledger.post_capture(db, mid, f"ch_{i}", 1000 + i) for i in range(3)]
    db.flush()
    r1 = client.get("/v1/balance_transactions", params={"limit": 1}, headers=h)
    assert ids(r1) == [bts[2].id] and r1.json()["has_more"] is True
    r2 = client.get(
        "/v1/balance_transactions", params={"limit": 1, "starting_after": bts[2].id}, headers=h
    )
    assert ids(r2) == [bts[1].id] and r2.json()["has_more"] is True
    r3 = client.get(
        "/v1/balance_transactions", params={"limit": 1, "starting_after": bts[1].id}, headers=h
    )
    assert ids(r3) == [bts[0].id] and r3.json()["has_more"] is False


def test_retrieve_by_id(client, h, db, mid, ledger):
    bt = ledger.post_capture(db, mid, "ch_1", 1000)
    db.flush()
    r = client.get(f"/v1/balance_transactions/{bt.id}", headers=h)
    assert r.status_code == 200, r.text
    assert set(r.json()) == BT_KEYS
    assert r.json()["id"] == bt.id
    assert r.json()["net"] == 941


def test_retrieve_unknown_is_404(client, h):
    r = client.get("/v1/balance_transactions/txn_doesnotexist", headers=h)
    assert r.status_code == 404
    assert err(r)["code"] == "resource_missing"


def test_retrieve_other_merchants_transaction_is_404(client, h, db, ledger):
    other, _, _ = seed.seed(db, "b@example.com")
    bt = ledger.post_capture(db, other.id, "ch_1", 1000)
    db.flush()
    r = client.get(f"/v1/balance_transactions/{bt.id}", headers=h)
    assert r.status_code == 404
    assert err(r)["code"] == "resource_missing"
    assert ids(client.get("/v1/balance_transactions", headers=h)) == []


@pytest.mark.parametrize(
    "path", ["/v1/balance", "/v1/balance_transactions", "/v1/balance_transactions/txn_x"]
)
def test_publishable_key_is_403(client, keys, path):
    r = client.get(path, headers={"Authorization": f"Bearer {keys[2]}"})
    assert r.status_code == 403
    assert err(r)["code"] == "permission_denied"
