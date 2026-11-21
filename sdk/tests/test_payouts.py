"""M23: client.payouts (create, retrieve, list, list_all)."""

import uuid

from tests.test_client import form, make, ok


def page(ids, has_more):
    return ok({"object": "list", "data": [{"id": i} for i in ids], "has_more": has_more})


def test_create_posts_form_with_idempotency_key():
    client, rec, _ = make(ok({"id": "po_1", "object": "payout"}))
    assert client.payouts.create(amount=500, currency="usd")["id"] == "po_1"
    req = rec.requests[0]
    assert (req.method, req.url.path) == ("POST", "/v1/payouts")
    assert form(req) == [("amount", "500"), ("currency", "usd")]
    assert uuid.UUID(req.headers["Idempotency-Key"]).version == 4


def test_create_uses_caller_idempotency_key():
    client, rec, _ = make()
    client.payouts.create(amount=500, currency="usd", idempotency_key="po-key-1")
    assert rec.requests[0].headers["Idempotency-Key"] == "po-key-1"


def test_retrieve_gets_by_id():
    client, rec, _ = make()
    client.payouts.retrieve("po_1")
    req = rec.requests[0]
    assert (req.method, req.url.path) == ("GET", "/v1/payouts/po_1")


def test_retrieve_quotes_the_id():
    client, rec, _ = make()
    client.payouts.retrieve("../balance")
    assert rec.requests[0].url.raw_path.decode() == "/v1/payouts/..%2Fbalance"


def test_list_gets_collection_with_params():
    client, rec, _ = make(page(["po_2", "po_1"], False))
    assert [p["id"] for p in client.payouts.list(limit=2)["data"]] == ["po_2", "po_1"]
    req = rec.requests[0]
    assert (req.method, req.url.path) == ("GET", "/v1/payouts")
    assert dict(req.url.params) == {"limit": "2"}


def test_list_all_follows_pages():
    client, rec, _ = make(page(["po_3", "po_2"], True), page(["po_1"], False))
    assert [p["id"] for p in client.payouts.list_all(limit=2)] == ["po_3", "po_2", "po_1"]
    assert [r.url.path for r in rec.requests] == ["/v1/payouts"] * 2
    assert rec.requests[1].url.params["starting_after"] == "po_2"
