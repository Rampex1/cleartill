"""M13: emit() writes one pending webhook delivery per matching endpoint (the outbox)."""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import auth, errors, executor, forms, models, seed
from app.db import get_db
from app.events import emit

URL = "https://93.184.216.34/hook"


@pytest.fixture
def a(db):
    merchant, sk, _ = seed.seed(db, "wd-a@example.com")
    return merchant, {"Authorization": f"Bearer {sk}"}


@pytest.fixture
def b(db):
    merchant, sk, _ = seed.seed(db, "wd-b@example.com")
    return merchant, {"Authorization": f"Bearer {sk}"}


def endpoint(client, h, events=("*",)):
    r = client.post(
        "/v1/webhook_endpoints", data={"url": URL, "enabled_events[]": list(events)}, headers=h
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def customer_created(client, h):
    """Create a customer; return the id of its customer.created event."""
    assert client.post("/v1/customers", headers=h).status_code == 200
    (e,) = client.get("/v1/events", headers=h).json()["data"]
    assert e["type"] == "customer.created"
    return e["id"]


def deliveries(db, merchant_id):
    D = models.WebhookDelivery
    return db.scalars(select(D).where(D.merchant_id == merchant_id)).all()


def pending(client, h, event_id):
    listed = {e["id"]: e for e in client.get("/v1/events", headers=h).json()["data"]}
    one = client.get(f"/v1/events/{event_id}", headers=h).json()
    assert listed[event_id]["pending_webhooks"] == one["pending_webhooks"]
    return one["pending_webhooks"]


# --- matching ------------------------------------------------------------------------------


def test_wildcard_endpoint_gets_one_pending_delivery(client, db, a):
    we = endpoint(client, a[1])
    evt = customer_created(client, a[1])
    (d,) = deliveries(db, a[0].id)
    assert d.id is not None
    assert d.event_id == evt and d.endpoint_id == we
    assert d.status == "pending" and d.attempts == 0
    assert d.created_at is not None and d.next_attempt_at is not None


def test_endpoint_subscribed_to_that_type_gets_a_delivery(client, db, a):
    endpoint(client, a[1], events=("customer.updated", "customer.created"))
    customer_created(client, a[1])
    assert len(deliveries(db, a[0].id)) == 1


def test_endpoint_not_subscribed_to_the_type_gets_none(client, db, a):
    endpoint(client, a[1], events=("customer.deleted",))
    customer_created(client, a[1])
    assert deliveries(db, a[0].id) == []


def test_disabled_endpoint_gets_none(client, db, a):
    we = endpoint(client, a[1])
    client.post(f"/v1/webhook_endpoints/{we}", data={"disabled": "true"}, headers=a[1])
    customer_created(client, a[1])
    assert deliveries(db, a[0].id) == []


def test_reenabled_endpoint_gets_deliveries_again(client, db, a):
    we = endpoint(client, a[1])
    client.post(f"/v1/webhook_endpoints/{we}", data={"disabled": "true"}, headers=a[1])
    client.post(f"/v1/webhook_endpoints/{we}", data={"disabled": "false"}, headers=a[1])
    customer_created(client, a[1])
    assert len(deliveries(db, a[0].id)) == 1


def test_deleted_endpoint_gets_none(client, db, a):
    we = endpoint(client, a[1])
    client.delete(f"/v1/webhook_endpoints/{we}", headers=a[1])
    customer_created(client, a[1])
    assert deliveries(db, a[0].id) == []


def test_two_matching_endpoints_get_two_deliveries(client, db, a):
    we1 = endpoint(client, a[1])
    we2 = endpoint(client, a[1], events=("customer.created",))
    evt = customer_created(client, a[1])
    rows = deliveries(db, a[0].id)
    assert sorted(d.endpoint_id for d in rows) == sorted([we1, we2])
    assert {d.event_id for d in rows} == {evt}
    assert pending(client, a[1], evt) == 2


def test_other_merchants_endpoint_gets_none(client, db, a, b):
    endpoint(client, b[1])
    customer_created(client, a[1])
    assert deliveries(db, a[0].id) == []
    assert deliveries(db, b[0].id) == []


def test_no_endpoints_means_no_deliveries(client, db, a):
    evt = customer_created(client, a[1])
    assert deliveries(db, a[0].id) == []
    assert pending(client, a[1], evt) == 0


def test_emit_without_request_also_creates_delivery(client, db, a):
    endpoint(client, a[1])
    e = emit(db, a[0].id, "payment_intent.canceled", {"id": "pi_x"})
    (d,) = deliveries(db, a[0].id)
    assert d.event_id == e.id and d.status == "pending"


# --- pending_webhooks ----------------------------------------------------------------------


def test_pending_webhooks_counts_pending_rows(client, a):
    endpoint(client, a[1])
    evt = customer_created(client, a[1])
    assert pending(client, a[1], evt) == 1


# --- disable / delete fail pending rows ----------------------------------------------------


def test_disabling_endpoint_fails_pending_deliveries(client, db, a):
    we = endpoint(client, a[1])
    evt = customer_created(client, a[1])
    r = client.post(f"/v1/webhook_endpoints/{we}", data={"disabled": "true"}, headers=a[1])
    assert r.status_code == 200
    db.expire_all()
    assert [d.status for d in deliveries(db, a[0].id)] == ["failed"]
    assert pending(client, a[1], evt) == 0


def test_deleting_endpoint_fails_pending_deliveries(client, db, a):
    we = endpoint(client, a[1])
    evt = customer_created(client, a[1])
    assert client.delete(f"/v1/webhook_endpoints/{we}", headers=a[1]).status_code == 200
    db.expire_all()
    assert [d.status for d in deliveries(db, a[0].id)] == ["failed"]
    assert pending(client, a[1], evt) == 0


def test_disabling_one_endpoint_leaves_others_pending(client, db, a):
    we1 = endpoint(client, a[1])
    we2 = endpoint(client, a[1])
    evt = customer_created(client, a[1])
    client.post(f"/v1/webhook_endpoints/{we1}", data={"disabled": "true"}, headers=a[1])
    db.expire_all()
    status = {d.endpoint_id: d.status for d in deliveries(db, a[0].id)}
    assert status == {we1: "failed", we2: "pending"}
    assert pending(client, a[1], evt) == 1


# --- rollback ------------------------------------------------------------------------------


def test_deliveries_emitted_then_rolled_back_are_discarded(client, db, a):
    endpoint(client, a[1])
    t = FastAPI()
    errors.install(t)

    @t.post("/t")
    def route(
        request: Request,
        session: Annotated[Session, Depends(get_db)],
        ctx: Annotated[auth.AuthContext, Depends(auth.require_secret_key)],
        params: Annotated[dict, Depends(forms.params)],
    ):
        def handler():
            emit(session, ctx.merchant.id, "customer.created", {"id": "x"}, request)
            raise errors.ApiError(400, "invalid_request_error", "nope")

        return executor.execute(session, request, ctx.merchant.id, params, handler)

    t.dependency_overrides[get_db] = lambda: (yield db)
    with TestClient(t) as c:
        r = c.post("/t", headers=a[1])
    assert r.status_code == 400
    n = db.scalar(
        text("SELECT count(*) FROM webhook_deliveries WHERE merchant_id = :m"), {"m": a[0].id}
    )
    assert n == 0
