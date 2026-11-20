# ruff: noqa: F811  (pytest fixtures shared from test_webhook_worker)
"""M20: dashboard webhook deliveries, attempts and manual resend (/dashboard-api).

Resend commits and makes a real HTTP call, so these tests use committed data (merchants made
through dashboard signup) and clean up after themselves, like test_webhook_worker."""

import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_webhook_worker import (
    CLEANUP,
    FORM,
    attempts,
    deliver_until_attempted,
    insecure,  # noqa: F401
    receiver_factory,  # noqa: F401
    row,
    sql,
)

from app import ratelimit, webhooks
from app.ids import new_id
from app.main import app

CSRF = {"X-Cleartill-Dashboard": "1"}
PW = "hunter2hunter2"


@pytest.fixture(autouse=True)
def _reset_ratelimit():
    ratelimit.reset()
    yield
    ratelimit.reset()


class Dash:
    def __init__(self, merchant_id, client, sk):
        self.id = merchant_id
        self.c = client  # cookie-authenticated
        self.sk = {"Authorization": f"Bearer {sk}", **FORM}


@pytest.fixture
def make_dash(engine):
    """Sign up committed merchants through the dashboard API (session cookie + keys)."""
    made = []

    def make():
        c = TestClient(app, base_url="https://testserver")
        c.__enter__()
        r = c.post(
            "/dashboard-api/signup",
            data={"email": f"{new_id('w20_').lower()}@example.com", "password": PW},
            headers=CSRF,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        d = Dash(body["merchant"]["id"], c, body["secret_key"])
        made.append(d)
        return d

    yield make
    for d in made:
        d.c.__exit__(None, None, None)
        with engine.begin() as conn:
            for table in (*CLEANUP, "dashboard_sessions"):
                conn.execute(text(f"DELETE FROM {table} WHERE merchant_id = :m"), {"m": d.id})
            conn.execute(text("DELETE FROM merchants WHERE id = :m"), {"m": d.id})


@pytest.fixture
def dash(make_dash):
    return make_dash()


def sk_post(d, path, body=""):
    with TestClient(app) as c:
        r = c.post(path, content=body, headers=d.sk)
    assert r.status_code == 200, r.text
    return r.json()


def endpoint(d, url):
    return sk_post(d, "/v1/webhook_endpoints", f"url={url}&enabled_events[]=*")


def customer(d):
    return sk_post(d, "/v1/customers")


def setup(d, url):
    """An endpoint at url plus one customer (one pending delivery). Returns (endpoint, wd)."""
    ep = endpoint(d, url)
    customer(d)
    deliveries = list_deliveries(d, ep["id"])
    assert len(deliveries) == 1, deliveries
    return ep, deliveries[0]


def list_deliveries(d, we_id):
    r = d.c.get(f"/dashboard-api/webhook_endpoints/{we_id}/deliveries")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "list"
    return body["data"]


def list_attempts(d, wd_id):
    r = d.c.get(f"/dashboard-api/webhook_deliveries/{wd_id}/attempts")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "list"
    return body["data"]


def resend(d, wd_id, headers=CSRF):
    return d.c.post(f"/dashboard-api/webhook_deliveries/{wd_id}/resend", headers=headers)


def err(r):
    return r.json()["error"]


# --- deliveries list -----------------------------------------------------------------------


def test_deliveries_item_shape(dash, insecure, receiver_factory):
    rx = receiver_factory(200)
    _, wd = setup(dash, rx.url)
    assert set(wd) == {
        "id",
        "object",
        "event",
        "type",
        "status",
        "attempts",
        "next_attempt_at",
        "created",
    }
    assert wd["object"] == "webhook_delivery"
    assert re.match(r"^evt_", wd["event"])
    assert wd["type"] == "customer.created"
    assert wd["status"] == "pending"
    assert wd["attempts"] == 0
    assert isinstance(wd["next_attempt_at"], int)
    assert isinstance(wd["created"], int)


def test_deliveries_newest_first(dash, insecure, receiver_factory):
    rx = receiver_factory(200)
    ep = endpoint(dash, rx.url)
    cus = customer(dash)
    sk_post(dash, f"/v1/customers/{cus['id']}", "name=Renamed")
    types = [wd["type"] for wd in list_deliveries(dash, ep["id"])]
    assert types == ["customer.updated", "customer.created"]


def test_deliveries_list_caps_at_100(dash, insecure, receiver_factory):
    rx = receiver_factory(200)
    ep = endpoint(dash, rx.url)
    for _ in range(101):
        customer(dash)
    assert len(list_deliveries(dash, ep["id"])) == 100


def test_deliveries_of_other_merchants_endpoint_is_404(make_dash, insecure, receiver_factory):
    owner, other = make_dash(), make_dash()
    ep, _ = setup(owner, receiver_factory(200).url)
    r = other.c.get(f"/dashboard-api/webhook_endpoints/{ep['id']}/deliveries")
    assert r.status_code == 404
    assert err(r)["code"] == "resource_missing"


def test_deliveries_with_api_key_is_401(dash, insecure, receiver_factory):
    ep, _ = setup(dash, receiver_factory(200).url)
    with TestClient(app, base_url="https://testserver") as c:
        r = c.get(f"/dashboard-api/webhook_endpoints/{ep['id']}/deliveries", headers=dash.sk)
    assert r.status_code == 401


# --- attempts list -------------------------------------------------------------------------


def test_attempts_empty_before_any_send(dash, insecure, receiver_factory):
    _, wd = setup(dash, receiver_factory(200).url)
    assert list_attempts(dash, wd["id"]) == []


def test_attempts_of_other_merchants_delivery_is_404(make_dash, insecure, receiver_factory):
    owner, other = make_dash(), make_dash()
    _, wd = setup(owner, receiver_factory(200).url)
    r = other.c.get(f"/dashboard-api/webhook_deliveries/{wd['id']}/attempts")
    assert r.status_code == 404


def test_attempts_with_api_key_is_401(dash, insecure, receiver_factory):
    _, wd = setup(dash, receiver_factory(200).url)
    with TestClient(app, base_url="https://testserver") as c:
        r = c.get(f"/dashboard-api/webhook_deliveries/{wd['id']}/attempts", headers=dash.sk)
    assert r.status_code == 401


def test_attempts_worker_then_manual_oldest_first(engine, dash, insecure, receiver_factory):
    rx = receiver_factory(500, 200)
    ep, wd = setup(dash, rx.url)
    deliver_until_attempted(engine, wd["id"])
    r = resend(dash, wd["id"])
    assert r.status_code == 200, r.text
    log = list_attempts(dash, wd["id"])
    assert [(a["manual"], a["status_code"]) for a in log] == [(False, 500), (True, 200)]
    for a in log:
        assert set(a) == {"id", "object", "at", "status_code", "error", "duration_ms", "manual"}
        assert a["object"] == "webhook_attempt"
        assert isinstance(a["at"], int)
        assert a["error"] is None
        assert isinstance(a["duration_ms"], int) and a["duration_ms"] >= 0
    assert log[0]["at"] <= log[1]["at"]
    assert list_deliveries(dash, ep["id"])[0]["status"] == "succeeded"


# --- resend --------------------------------------------------------------------------------


def test_resend_success_marks_succeeded(engine, dash, insecure, receiver_factory):
    rx = receiver_factory(200)
    ep, wd = setup(dash, rx.url)
    r = resend(dash, wd["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["delivery"]["id"] == wd["id"]
    assert body["delivery"]["object"] == "webhook_delivery"
    assert body["delivery"]["status"] == "succeeded"
    assert body["attempt"]["object"] == "webhook_attempt"
    assert body["attempt"]["manual"] is True
    assert body["attempt"]["status_code"] == 200
    assert len(rx.requests) == 1
    assert row(engine, wd["id"])["status"] == "succeeded"
    assert list_deliveries(dash, ep["id"])[0]["status"] == "succeeded"


def test_resend_sends_the_event_signed_with_endpoint_secret(dash, insecure, receiver_factory):
    rx = receiver_factory(200)
    ep, wd = setup(dash, rx.url)
    assert resend(dash, wd["id"]).status_code == 200
    (req,) = rx.requests
    event = json.loads(req["body"])
    assert event["id"] == wd["event"]
    assert event["type"] == "customer.created"
    assert webhooks.verify(req["body"], req["headers"][webhooks.SIGNATURE_HEADER], ep["secret"])


def test_resend_non_2xx_returns_200_and_leaves_delivery_unchanged(
    engine, dash, insecure, receiver_factory
):
    rx = receiver_factory(500)
    ep, wd = setup(dash, rx.url)
    before = row(engine, wd["id"])
    r = resend(dash, wd["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["attempt"]["status_code"] == 500
    assert body["attempt"]["manual"] is True
    for key in ("status", "attempts", "next_attempt_at"):
        assert body["delivery"][key] == wd[key], key
    after = row(engine, wd["id"])
    for key in ("status", "attempts", "next_attempt_at"):
        assert after[key] == before[key], key
    assert list_deliveries(dash, ep["id"])[0] == wd
    assert len(rx.requests) == 1


def test_resend_failed_delivery_then_2xx_succeeds(engine, dash, insecure, receiver_factory):
    rx = receiver_factory(200)
    _, wd = setup(dash, rx.url)
    sql(engine, "UPDATE webhook_deliveries SET status = 'failed' WHERE id = :d", d=wd["id"])
    r = resend(dash, wd["id"])
    assert r.status_code == 200, r.text
    assert r.json()["delivery"]["status"] == "succeeded"
    assert row(engine, wd["id"])["status"] == "succeeded"


def test_resend_while_claimed_is_409_and_sends_nothing(engine, dash, insecure, receiver_factory):
    rx = receiver_factory(200)
    _, wd = setup(dash, rx.url)
    sql(
        engine,
        "UPDATE webhook_deliveries SET locked_until = now() + interval '60 seconds' WHERE id = :d",
        d=wd["id"],
    )
    r = resend(dash, wd["id"])
    assert r.status_code == 409
    assert err(r)["code"] == "delivery_in_progress"
    assert rx.requests == []
    assert attempts(engine, wd["id"]) == []


def test_resend_after_endpoint_deleted_is_400(dash, insecure, receiver_factory):
    rx = receiver_factory(200)
    ep, wd = setup(dash, rx.url)
    with TestClient(app) as c:
        assert c.delete(f"/v1/webhook_endpoints/{ep['id']}", headers=dash.sk).status_code == 200
    r = resend(dash, wd["id"])
    assert r.status_code == 400


def test_resend_without_csrf_header_is_403(dash, insecure, receiver_factory):
    rx = receiver_factory(200)
    _, wd = setup(dash, rx.url)
    r = resend(dash, wd["id"], headers={})
    assert r.status_code == 403
    assert rx.requests == []


def test_resend_other_merchants_delivery_is_404(make_dash, insecure, receiver_factory):
    owner, other = make_dash(), make_dash()
    rx = receiver_factory(200)
    _, wd = setup(owner, rx.url)
    r = resend(other, wd["id"])
    assert r.status_code == 404
    assert rx.requests == []


def test_resend_with_api_key_is_401(dash, insecure, receiver_factory):
    rx = receiver_factory(200)
    _, wd = setup(dash, rx.url)
    with TestClient(app, base_url="https://testserver") as c:
        r = c.post(
            f"/dashboard-api/webhook_deliveries/{wd['id']}/resend", headers={**dash.sk, **CSRF}
        )
    assert r.status_code == 401
    assert rx.requests == []
