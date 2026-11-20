"""M13: webhook endpoint CRUD."""

import pytest

from app import seed

URL = "https://93.184.216.34/hook"
KEYS = {"id", "object", "url", "enabled_events", "description", "status", "created", "livemode"}


@pytest.fixture
def keys(db):
    return seed.seed(db, "we-a@example.com")


@pytest.fixture
def h(keys):
    return {"Authorization": f"Bearer {keys[1]}"}


@pytest.fixture
def other(db):
    _, sk, _ = seed.seed(db, "we-b@example.com")
    return {"Authorization": f"Bearer {sk}"}


def err(r):
    return r.json()["error"]


def create(client, h, events=("*",), **data):
    r = client.post(
        "/v1/webhook_endpoints",
        data={"url": URL, "enabled_events[]": list(events), **data},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def path(we):
    return f"/v1/webhook_endpoints/{we['id']}"


def listed(client, h):
    r = client.get("/v1/webhook_endpoints", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]


# --- create --------------------------------------------------------------------------------


def test_create_returns_endpoint_with_secret(client, h):
    we = create(client, h, description="main")
    assert set(we) == KEYS | {"secret"}
    assert we["id"].startswith("we_")
    assert we["object"] == "webhook_endpoint"
    assert we["url"] == URL
    assert we["enabled_events"] == ["*"]
    assert we["description"] == "main"
    assert we["status"] == "enabled"
    assert isinstance(we["created"], int) and not isinstance(we["created"], bool)
    assert we["livemode"] is False
    assert we["secret"].startswith("whsec_")


def test_create_without_description_is_null(client, h):
    assert create(client, h)["description"] is None


def test_create_with_specific_event_types(client, h):
    we = create(client, h, events=("customer.created", "charge.refunded"))
    assert we["enabled_events"] == ["customer.created", "charge.refunded"]


def test_retrieve_has_no_secret(client, h):
    we = create(client, h)
    r = client.get(path(we), headers=h)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == KEYS
    assert body["id"] == we["id"] and body["url"] == URL


def test_list_has_no_secret(client, h):
    we = create(client, h)
    (item,) = listed(client, h)
    assert set(item) == KEYS and item["id"] == we["id"]


def test_secrets_are_unique_per_endpoint(client, h):
    assert create(client, h)["secret"] != create(client, h)["secret"]


# --- validation ----------------------------------------------------------------------------


def test_missing_enabled_events_is_400(client, h):
    r = client.post("/v1/webhook_endpoints", data={"url": URL}, headers=h)
    assert r.status_code == 400 and err(r)["type"] == "invalid_request_error"


def test_empty_string_event_is_400(client, h):
    r = client.post("/v1/webhook_endpoints", data={"url": URL, "enabled_events[]": [""]}, headers=h)
    assert r.status_code == 400


def test_unknown_event_type_is_400(client, h):
    r = client.post(
        "/v1/webhook_endpoints",
        data={"url": URL, "enabled_events[]": ["customer.created", "customer.exploded"]},
        headers=h,
    )
    assert r.status_code == 400 and err(r)["type"] == "invalid_request_error"
    assert listed(client, h) == []


def test_missing_url_is_400(client, h):
    r = client.post("/v1/webhook_endpoints", data={"enabled_events[]": ["*"]}, headers=h)
    assert r.status_code == 400


# --- update --------------------------------------------------------------------------------


def test_update_url_and_events(client, h):
    we = create(client, h)
    r = client.post(
        path(we),
        data={"url": "https://8.8.8.8/new", "enabled_events[]": ["refund.created"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert set(r.json()) == KEYS
    got = client.get(path(we), headers=h).json()
    assert got["url"] == "https://8.8.8.8/new"
    assert got["enabled_events"] == ["refund.created"]


def test_update_description(client, h):
    we = create(client, h)
    assert client.post(path(we), data={"description": "d2"}, headers=h).json()["description"] == (
        "d2"
    )


def test_update_with_unknown_event_type_is_400(client, h):
    we = create(client, h)
    r = client.post(path(we), data={"enabled_events[]": ["nope.nope"]}, headers=h)
    assert r.status_code == 400
    assert client.get(path(we), headers=h).json()["enabled_events"] == ["*"]


def test_disable_then_reenable(client, h):
    we = create(client, h)
    r = client.post(path(we), data={"disabled": "true"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "disabled"
    assert client.get(path(we), headers=h).json()["status"] == "disabled"
    r = client.post(path(we), data={"disabled": "false"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "enabled"


# --- delete --------------------------------------------------------------------------------


def test_delete_response_shape(client, h):
    we = create(client, h)
    r = client.delete(path(we), headers=h)
    assert r.status_code == 200
    assert r.json() == {"id": we["id"], "object": "webhook_endpoint", "deleted": True}


def test_deleted_endpoint_is_404_and_not_listed(client, h):
    we = create(client, h)
    keep = create(client, h)
    client.delete(path(we), headers=h)
    r = client.get(path(we), headers=h)
    assert r.status_code == 404 and err(r)["type"] == "invalid_request_error"
    assert [e["id"] for e in listed(client, h)] == [keep["id"]]


def test_second_delete_is_404(client, h):
    we = create(client, h)
    client.delete(path(we), headers=h)
    assert client.delete(path(we), headers=h).status_code == 404


def test_update_deleted_endpoint_is_404(client, h):
    we = create(client, h)
    client.delete(path(we), headers=h)
    assert client.post(path(we), data={"description": "x"}, headers=h).status_code == 404


def test_unknown_id_is_404(client, h):
    assert client.get("/v1/webhook_endpoints/we_nope", headers=h).status_code == 404


# --- access --------------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_other_merchants_endpoint_is_404(client, h, other, method):
    we = create(client, h)
    data = {"description": "x"} if method == "POST" else None
    r = client.request(method, path(we), data=data, headers=other)
    assert r.status_code == 404
    assert client.get(path(we), headers=h).json()["description"] is None


def test_other_merchant_list_is_empty(client, h, other):
    create(client, h)
    assert listed(client, other) == []


@pytest.mark.parametrize(
    ("method", "p"),
    [
        ("POST", "/v1/webhook_endpoints"),
        ("GET", "/v1/webhook_endpoints"),
        ("GET", "/v1/webhook_endpoints/{id}"),
        ("POST", "/v1/webhook_endpoints/{id}"),
        ("DELETE", "/v1/webhook_endpoints/{id}"),
    ],
)
def test_publishable_key_is_403(client, keys, h, method, p):
    we = create(client, h)
    r = client.request(
        method, p.format(id=we["id"]), headers={"Authorization": f"Bearer {keys[2]}"}
    )
    assert r.status_code == 403
    assert err(r)["code"] == "permission_denied"
