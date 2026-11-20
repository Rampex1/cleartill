"""M7: Stripe-style events emitted on customer changes, and the Events API."""

import pytest

from app import seed

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
EVENT_KEYS = {
    "id",
    "object",
    "api_version",
    "created",
    "data",
    "livemode",
    "pending_webhooks",
    "request",
    "type",
}


@pytest.fixture
def keys(db):
    return seed.seed(db, "events-a@example.com")


@pytest.fixture
def h(keys):
    return {"Authorization": f"Bearer {keys[1]}"}


@pytest.fixture
def hf(h):
    return {**h, **FORM}


def err(r):
    return r.json()["error"]


def create(client, h, **data):
    r = client.post("/v1/customers", data=data, headers=h)
    assert r.status_code == 200, r.text
    return r


def events(client, h, **params):
    r = client.get("/v1/events", params=params, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def types(evts):
    return [e["type"] for e in evts]


# --- emission ------------------------------------------------------------------------------


def test_create_emits_one_customer_created_event(client, h):
    r = create(client, h, email="x@example.com", name="Ada", **{"metadata[k]": "v"})
    (e,) = events(client, h)
    assert set(e) == EVENT_KEYS
    assert e["id"].startswith("evt_")
    assert e["object"] == "event"
    assert e["type"] == "customer.created"
    assert e["data"] == {"object": r.json()}
    assert e["api_version"] == "2026-11-19"
    assert e["livemode"] is False
    assert e["pending_webhooks"] == 0
    assert isinstance(e["created"], int) and not isinstance(e["created"], bool)


def test_event_request_id_matches_response_header(client, h):
    r = create(client, h)
    (e,) = events(client, h)
    assert e["request"]["id"].startswith("req_")
    assert e["request"]["id"] == r.headers["Request-Id"]


def test_event_idempotency_key_is_none_without_header(client, h):
    create(client, h)
    (e,) = events(client, h)
    assert e["request"]["idempotency_key"] is None


def test_event_idempotency_key_matches_header(client, hf):
    r = client.post("/v1/customers", content="", headers={**hf, "Idempotency-Key": "ik-1"})
    assert r.status_code == 200
    (e,) = events(client, hf)
    assert e["request"] == {"id": r.headers["Request-Id"], "idempotency_key": "ik-1"}


def test_update_emits_customer_updated_with_new_snapshot(client, h, hf):
    cid = create(client, h).json()["id"]
    r = client.post(f"/v1/customers/{cid}", content="name=New", headers=hf)
    assert r.status_code == 200
    (e,) = events(client, h, type="customer.updated")
    assert e["data"]["object"]["name"] == "New"
    assert e["data"]["object"] == r.json()


def test_delete_emits_customer_deleted_with_full_customer(client, h):
    cid = create(client, h, email="x@example.com").json()["id"]
    assert client.delete(f"/v1/customers/{cid}", headers=h).status_code == 200
    (e,) = events(client, h, type="customer.deleted")
    obj = e["data"]["object"]
    assert obj["id"] == cid and obj["email"] == "x@example.com"
    assert "deleted" not in obj
    assert set(obj) == {"id", "object", "created", "livemode", "email", "name", "metadata"}


def test_events_are_listed_newest_first(client, h, hf):
    cid = create(client, h).json()["id"]
    client.post(f"/v1/customers/{cid}", content="name=N", headers=hf)
    client.delete(f"/v1/customers/{cid}", headers=h)
    assert types(events(client, h)) == [
        "customer.deleted",
        "customer.updated",
        "customer.created",
    ]


def test_event_snapshot_is_not_changed_by_later_update(client, h, hf):
    cid = create(client, h, name="A").json()["id"]
    client.post(f"/v1/customers/{cid}", content="name=B", headers=hf)
    (e,) = events(client, h, type="customer.created")
    assert e["data"]["object"]["name"] == "A"


# --- failures and replays emit nothing -----------------------------------------------------


def test_failed_create_emits_nothing(client, h):
    r = client.post("/v1/customers", data={"email": "a" * 513}, headers=h)
    assert r.status_code == 400
    assert events(client, h) == []


def test_update_of_unknown_customer_emits_nothing(client, h, hf):
    create(client, h)
    r = client.post("/v1/customers/cus_" + "0" * 24, content="name=x", headers=hf)
    assert r.status_code == 404
    assert len(events(client, h)) == 1


def test_delete_of_deleted_customer_emits_nothing(client, h):
    cid = create(client, h).json()["id"]
    client.delete(f"/v1/customers/{cid}", headers=h)
    before = len(events(client, h))
    assert client.delete(f"/v1/customers/{cid}", headers=h).status_code == 404
    assert len(events(client, h)) == before == 2


def test_idempotent_replay_emits_nothing_new(client, hf):
    h = {**hf, "Idempotency-Key": "k1"}
    client.post("/v1/customers", content="email=a@x.com", headers=h)
    r = client.post("/v1/customers", content="email=a@x.com", headers=h)
    assert r.headers.get("Idempotent-Replayed") == "true"
    assert types(events(client, hf)) == ["customer.created"]


# --- list filters and pagination -----------------------------------------------------------


def test_type_filter_returns_only_matching_events(client, h, hf):
    a = create(client, h).json()["id"]
    b = create(client, h).json()["id"]
    client.post(f"/v1/customers/{a}", content="name=1", headers=hf)
    client.post(f"/v1/customers/{b}", content="name=2", headers=hf)
    got = events(client, h, type="customer.updated")
    assert types(got) == ["customer.updated", "customer.updated"]
    assert [e["data"]["object"]["id"] for e in got] == [b, a]


def test_type_filter_unknown_type_is_empty(client, h):
    create(client, h)
    r = client.get("/v1/events", params={"type": "nope"}, headers=h)
    assert r.status_code == 200
    assert r.json() == {"object": "list", "data": [], "has_more": False, "url": "/v1/events"}


def test_list_shape(client, h):
    create(client, h)
    body = client.get("/v1/events", headers=h).json()
    assert body["object"] == "list" and body["url"] == "/v1/events"
    assert body["has_more"] is False and len(body["data"]) == 1


def test_pagination_with_limit_and_starting_after(client, h):
    for _ in range(3):
        create(client, h)
    all_ids = [e["id"] for e in events(client, h)]
    assert len(all_ids) == 3
    r = client.get("/v1/events", params={"limit": 1}, headers=h).json()
    assert r["has_more"] is True and [e["id"] for e in r["data"]] == all_ids[:1]
    r = client.get(
        "/v1/events", params={"limit": 1, "starting_after": all_ids[0]}, headers=h
    ).json()
    assert r["has_more"] is True and [e["id"] for e in r["data"]] == all_ids[1:2]
    r = client.get("/v1/events", params={"starting_after": all_ids[1]}, headers=h).json()
    assert r["has_more"] is False and [e["id"] for e in r["data"]] == all_ids[2:]


def test_pagination_ending_before(client, h):
    for _ in range(3):
        create(client, h)
    all_ids = [e["id"] for e in events(client, h)]
    got = events(client, h, ending_before=all_ids[2])
    assert [e["id"] for e in got] == all_ids[:2]


# --- retrieve ------------------------------------------------------------------------------


def test_retrieve_matches_list_item(client, h):
    create(client, h, name="Ada")
    (e,) = events(client, h)
    r = client.get(f"/v1/events/{e['id']}", headers=h)
    assert r.status_code == 200
    assert r.json() == e


def test_retrieve_unknown_is_404(client, h):
    r = client.get("/v1/events/evt_" + "0" * 24, headers=h)
    assert r.status_code == 404
    assert err(r)["type"] == "invalid_request_error"
    assert err(r)["code"] == "resource_missing"


# --- tenancy and auth ----------------------------------------------------------------------


def test_other_merchant_cannot_retrieve_event(client, db, h):
    create(client, h)
    (e,) = events(client, h)
    _, sk_b, _ = seed.seed(db, "events-b@example.com")
    hb = {"Authorization": f"Bearer {sk_b}"}
    r = client.get(f"/v1/events/{e['id']}", headers=hb)
    assert r.status_code == 404 and err(r)["code"] == "resource_missing"


def test_other_merchant_list_excludes_event(client, db, h):
    create(client, h)
    (e,) = events(client, h)
    _, sk_b, _ = seed.seed(db, "events-b@example.com")
    hb = {"Authorization": f"Bearer {sk_b}"}
    create(client, hb)
    got = events(client, hb)
    assert e["id"] not in [x["id"] for x in got]
    assert types(got) == ["customer.created"]


@pytest.mark.parametrize("path", ["/v1/events", "/v1/events/{id}"])
def test_publishable_key_is_403(client, keys, h, path):
    create(client, h)
    (e,) = events(client, h)
    r = client.get(path.format(id=e["id"]), headers={"Authorization": f"Bearer {keys[2]}"})
    assert r.status_code == 403
    assert err(r)["code"] == "permission_denied"
