import re

import pytest
from sqlalchemy import select

from app import models, seed

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
CUS_ID = re.compile(r"^cus_[0-9A-Za-z]{24}$")
KEYS = {"id", "object", "created", "livemode", "email", "name", "metadata"}


@pytest.fixture
def keys(db):
    return seed.seed(db, "a@example.com")


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
    return r.json()


def ids(r):
    return [c["id"] for c in r.json()["data"]]


# --- create --------------------------------------------------------------------------------


def test_create_returns_customer(client, h):
    c = create(client, h, email="x@example.com", name="Ada", **{"metadata[k]": "v"})
    assert set(c) == KEYS
    assert CUS_ID.match(c["id"])
    assert c["object"] == "customer"
    assert isinstance(c["created"], int) and not isinstance(c["created"], bool)
    assert c["livemode"] is False
    assert c["email"] == "x@example.com"
    assert c["name"] == "Ada"
    assert c["metadata"] == {"k": "v"}


def test_create_without_params_has_nulls_and_empty_metadata(client, h):
    c = create(client, h)
    assert c["email"] is None and c["name"] is None and c["metadata"] == {}


def test_create_with_empty_email_and_name_stores_null(client, hf):
    r = client.post("/v1/customers", content="email=&name=", headers=hf)
    assert r.status_code == 200
    assert r.json()["email"] is None and r.json()["name"] is None


def test_create_unknown_param_is_parameter_unknown(client, h):
    r = client.post("/v1/customers", data={"foo": "1"}, headers=h)
    assert r.status_code == 400
    assert err(r)["code"] == "parameter_unknown"
    assert err(r)["param"] == "foo"


def test_create_email_too_long(client, h):
    r = client.post("/v1/customers", data={"email": "a" * 513}, headers=h)
    assert r.status_code == 400
    assert err(r)["code"] == "parameter_invalid"
    assert err(r)["param"] == "email"


def test_create_email_at_max_length_ok(client, h):
    assert create(client, h, email="a" * 512)["email"] == "a" * 512


def test_create_name_too_long(client, h):
    r = client.post("/v1/customers", data={"name": "a" * 257}, headers=h)
    assert r.status_code == 400
    assert err(r)["param"] == "name"


def test_create_51_metadata_keys_rejected(client, h):
    data = {f"metadata[k{i}]": "v" for i in range(51)}
    r = client.post("/v1/customers", data=data, headers=h)
    assert r.status_code == 400
    assert err(r)["type"] == "invalid_request_error"


def test_create_50_metadata_keys_ok(client, h):
    c = create(client, h, **{f"metadata[k{i}]": "v" for i in range(50)})
    assert len(c["metadata"]) == 50


def test_create_metadata_key_too_long(client, h):
    key = "k" * 41
    r = client.post("/v1/customers", data={f"metadata[{key}]": "v"}, headers=h)
    assert r.status_code == 400
    assert err(r)["param"] == f"metadata[{key}]"


def test_create_metadata_value_too_long(client, h):
    r = client.post("/v1/customers", data={"metadata[k]": "v" * 501}, headers=h)
    assert r.status_code == 400
    assert err(r)["type"] == "invalid_request_error"


def test_create_metadata_limits_inclusive(client, h):
    c = create(client, h, **{f"metadata[{'k' * 40}]": "v" * 500})
    assert c["metadata"] == {"k" * 40: "v" * 500}


def test_create_numeric_metadata_key_stays_string(client, h):
    assert create(client, h, **{"metadata[12345]": "x"})["metadata"] == {"12345": "x"}


def test_create_unicode_values_round_trip(client, h):
    c = create(client, h, name="Zoë 東京", **{"metadata[ключ]": "значение"})
    r = client.get(f"/v1/customers/{c['id']}", headers=h)
    assert r.json()["name"] == "Zoë 東京"
    assert r.json()["metadata"] == {"ключ": "значение"}


# --- retrieve ------------------------------------------------------------------------------


def test_retrieve_matches_create(client, h):
    c = create(client, h, email="x@example.com", name="Ada", **{"metadata[k]": "v"})
    r = client.get(f"/v1/customers/{c['id']}", headers=h)
    assert r.status_code == 200
    assert r.json() == c


def test_retrieve_unknown_is_404(client, h):
    r = client.get("/v1/customers/cus_" + "0" * 24, headers=h)
    assert r.status_code == 404
    assert err(r)["type"] == "invalid_request_error"
    assert err(r)["code"] == "resource_missing"


def test_retrieve_deleted_returns_stub(client, h):
    c = create(client, h, name="Ada")
    client.delete(f"/v1/customers/{c['id']}", headers=h)
    r = client.get(f"/v1/customers/{c['id']}", headers=h)
    assert r.status_code == 200
    assert r.json() == {"id": c["id"], "object": "customer", "deleted": True}


# --- update --------------------------------------------------------------------------------


def update(client, hf, cid, body):
    return client.post(f"/v1/customers/{cid}", content=body, headers=hf)


def test_update_changes_only_sent_fields(client, h, hf):
    c = create(client, h, email="x@example.com", name="Ada", **{"metadata[k]": "v"})
    r = update(client, hf, c["id"], "name=Grace")
    assert r.status_code == 200
    assert r.json() == {**c, "name": "Grace"}
    assert client.get(f"/v1/customers/{c['id']}", headers=h).json() == {**c, "name": "Grace"}


def test_update_empty_name_sets_null(client, h, hf):
    c = create(client, h, email="x@example.com", name="Ada")
    r = update(client, hf, c["id"], "name=")
    assert r.json()["name"] is None
    assert r.json()["email"] == "x@example.com"


def test_update_empty_email_sets_null(client, h, hf):
    c = create(client, h, email="x@example.com", name="Ada")
    r = update(client, hf, c["id"], "email=")
    assert r.json()["email"] is None
    assert r.json()["name"] == "Ada"


def test_update_metadata_merges(client, h, hf):
    c = create(client, h, **{"metadata[a]": "1"})
    r = update(client, hf, c["id"], "metadata[b]=2")
    assert r.json()["metadata"] == {"a": "1", "b": "2"}


def test_update_metadata_overwrites_existing_key(client, h, hf):
    c = create(client, h, **{"metadata[a]": "1"})
    assert update(client, hf, c["id"], "metadata[a]=9").json()["metadata"] == {"a": "9"}


def test_update_metadata_empty_value_removes_key(client, h, hf):
    c = create(client, h, **{"metadata[a]": "1", "metadata[b]": "2"})
    r = update(client, hf, c["id"], "metadata[a]=")
    assert r.json()["metadata"] == {"b": "2"}
    assert client.get(f"/v1/customers/{c['id']}", headers=h).json()["metadata"] == {"b": "2"}


def test_update_metadata_empty_clears_all(client, h, hf):
    c = create(client, h, **{"metadata[a]": "1", "metadata[b]": "2"})
    r = update(client, hf, c["id"], "metadata=")
    assert r.json()["metadata"] == {}
    assert client.get(f"/v1/customers/{c['id']}", headers=h).json()["metadata"] == {}


def test_update_metadata_over_50_after_merge_rejected(client, h, hf):
    c = create(client, h, **{f"metadata[k{i}]": "v" for i in range(50)})
    r = update(client, hf, c["id"], "metadata[new]=v")
    assert r.status_code == 400
    assert err(r)["type"] == "invalid_request_error"
    assert len(client.get(f"/v1/customers/{c['id']}", headers=h).json()["metadata"]) == 50


def test_update_unknown_param_rejected(client, h, hf):
    c = create(client, h)
    r = update(client, hf, c["id"], "foo=1")
    assert r.status_code == 400 and err(r)["code"] == "parameter_unknown"


def test_update_deleted_is_404(client, h, hf):
    c = create(client, h)
    client.delete(f"/v1/customers/{c['id']}", headers=h)
    r = update(client, hf, c["id"], "name=x")
    assert r.status_code == 404
    assert err(r)["code"] == "resource_missing"


def test_update_unknown_is_404(client, hf):
    r = update(client, hf, "cus_" + "0" * 24, "name=x")
    assert r.status_code == 404 and err(r)["code"] == "resource_missing"


# --- delete --------------------------------------------------------------------------------


def test_delete_response_shape(client, h):
    c = create(client, h)
    r = client.delete(f"/v1/customers/{c['id']}", headers=h)
    assert r.status_code == 200
    assert r.json() == {"id": c["id"], "object": "customer", "deleted": True}


def test_delete_twice_is_404(client, h):
    c = create(client, h)
    client.delete(f"/v1/customers/{c['id']}", headers=h)
    r = client.delete(f"/v1/customers/{c['id']}", headers=h)
    assert r.status_code == 404 and err(r)["code"] == "resource_missing"


def test_delete_keeps_row_soft_deleted(client, db, h):
    c = create(client, h)
    client.delete(f"/v1/customers/{c['id']}", headers=h)
    db.expire_all()
    row = db.scalar(select(models.Customer).where(models.Customer.id == c["id"]))
    assert row is not None and row.deleted is True


# --- list ----------------------------------------------------------------------------------


def test_list_newest_first(client, h):
    made = [create(client, h)["id"] for _ in range(3)]
    r = client.get("/v1/customers", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert body["url"] == "/v1/customers"
    assert body["has_more"] is False
    assert ids(r) == made[::-1]
    assert all(set(c) == KEYS for c in body["data"])


def test_list_empty(client, h):
    r = client.get("/v1/customers", headers=h)
    assert r.json() == {"object": "list", "data": [], "has_more": False, "url": "/v1/customers"}


def test_list_default_limit_is_10(client, h):
    made = [create(client, h)["id"] for _ in range(11)]
    r = client.get("/v1/customers", headers=h)
    assert ids(r) == made[::-1][:10]
    assert r.json()["has_more"] is True


@pytest.mark.parametrize("limit", ["0", "101", "abc", "-1"])
def test_list_limit_out_of_range_is_400(client, h, limit):
    r = client.get("/v1/customers", params={"limit": limit}, headers=h)
    assert r.status_code == 400
    assert err(r)["param"] == "limit"


@pytest.mark.parametrize("limit", [1, 100])
def test_list_limit_bounds_ok(client, h, limit):
    create(client, h)
    r = client.get("/v1/customers", params={"limit": limit}, headers=h)
    assert r.status_code == 200 and len(r.json()["data"]) == 1


def test_list_excludes_deleted(client, h):
    keep, gone = create(client, h)["id"], create(client, h)["id"]
    client.delete(f"/v1/customers/{gone}", headers=h)
    assert ids(client.get("/v1/customers", headers=h)) == [keep]


def test_list_email_filter_is_exact(client, h):
    upper = create(client, h, email="A@x.com")["id"]
    lower = create(client, h, email="a@x.com")["id"]
    create(client, h, email="a@x.co")
    assert ids(client.get("/v1/customers", params={"email": "a@x.com"}, headers=h)) == [lower]
    assert ids(client.get("/v1/customers", params={"email": "A@x.com"}, headers=h)) == [upper]


# --- pagination ----------------------------------------------------------------------------


@pytest.fixture
def five(client, h):
    return [create(client, h)["id"] for _ in range(5)]  # c1..c5, c5 newest


def page(client, h, **params):
    r = client.get("/v1/customers", params=params, headers=h)
    assert r.status_code == 200, r.text
    return ids(r), r.json()["has_more"]


def test_first_page(client, h, five):
    c1, c2, c3, c4, c5 = five
    assert page(client, h, limit=2) == ([c5, c4], True)


def test_starting_after_middle(client, h, five):
    c1, c2, c3, c4, c5 = five
    assert page(client, h, starting_after=c4, limit=2) == ([c3, c2], True)


def test_starting_after_last_page(client, h, five):
    c1, c2, c3, c4, c5 = five
    assert page(client, h, starting_after=c2) == ([c1], False)


def test_starting_after_oldest_is_empty(client, h, five):
    assert page(client, h, starting_after=five[0]) == ([], False)


def test_ending_before_middle(client, h, five):
    c1, c2, c3, c4, c5 = five
    assert page(client, h, ending_before=c2, limit=2) == ([c4, c3], True)


def test_ending_before_first_page(client, h, five):
    c1, c2, c3, c4, c5 = five
    assert page(client, h, ending_before=c4) == ([c5], False)


def test_ending_before_newest_is_empty(client, h, five):
    assert page(client, h, ending_before=five[4]) == ([], False)


def test_both_cursors_is_400(client, h, five):
    r = client.get(
        "/v1/customers", params={"starting_after": five[3], "ending_before": five[1]}, headers=h
    )
    assert r.status_code == 400
    assert err(r)["type"] == "invalid_request_error"


@pytest.mark.parametrize("param", ["starting_after", "ending_before"])
def test_unknown_cursor_is_404_with_param(client, h, five, param):
    r = client.get("/v1/customers", params={param: "cus_" + "0" * 24}, headers=h)
    assert r.status_code == 404
    assert err(r)["code"] == "resource_missing"
    assert err(r)["param"] == param


def test_cursor_on_deleted_customer_still_works(client, h, five):
    c1, c2, c3, c4, c5 = five
    client.delete(f"/v1/customers/{c3}", headers=h)
    assert page(client, h, starting_after=c3) == ([c2, c1], False)


def test_walking_pages_visits_every_customer_once(client, h, five):
    seen, cursor = [], None
    while True:
        params = {"limit": 2, **({"starting_after": cursor} if cursor else {})}
        got, more = page(client, h, **params)
        seen += got
        if not more:
            break
        cursor = got[-1]
    assert seen == five[::-1]


# --- auth ----------------------------------------------------------------------------------


def test_no_key_is_401(client, keys):
    r = client.post("/v1/customers")
    assert r.status_code == 401
    assert err(r)["code"] == "api_key_invalid"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v1/customers"),
        ("GET", "/v1/customers"),
        ("GET", "/v1/customers/{id}"),
        ("POST", "/v1/customers/{id}"),
        ("DELETE", "/v1/customers/{id}"),
    ],
)
def test_publishable_key_is_403(client, keys, h, method, path):
    cid = create(client, h)["id"]
    r = client.request(method, path.format(id=cid), headers={"Authorization": f"Bearer {keys[2]}"})
    assert r.status_code == 403
    assert err(r)["code"] == "permission_denied"
