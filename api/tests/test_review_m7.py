"""Regression tests for the M7 events review."""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, errors, executor, forms, seed
from app.db import get_db
from app.events import emit
from app.models import Event

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@pytest.fixture
def a(db):
    merchant, sk, _ = seed.seed(db, "e7a@example.com")
    return merchant, {"Authorization": f"Bearer {sk}"}


@pytest.fixture
def b(db):
    merchant, sk, _ = seed.seed(db, "e7b@example.com")
    return merchant, {"Authorization": f"Bearer {sk}"}


def events(client, headers, **params):
    return client.get("/v1/events", params=params, headers=headers).json()["data"]


def test_event_emitted_then_rolled_back_is_discarded(db, a):
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
        r = c.post("/t", headers={**a[1], "Idempotency-Key": "rb"})
    assert r.status_code == 400
    assert db.scalars(select(Event).where(Event.merchant_id == a[0].id)).all() == []


def test_emit_without_request_records_null_request(db, a):
    e = emit(db, a[0].id, "payment_intent.canceled", {"id": "pi_x"})
    assert e.request == {"id": None, "idempotency_key": None}


def test_emits_within_one_transaction_keep_their_order(db, a):
    first = emit(db, a[0].id, "charge.succeeded", {"id": "ch_x"})
    second = emit(db, a[0].id, "payment_intent.succeeded", {"id": "pi_x"})
    assert first.seq < second.seq


def test_delete_with_idempotency_header_records_no_key(client, a):
    cus = client.post("/v1/customers", headers=a[1]).json()["id"]
    client.delete(f"/v1/customers/{cus}", headers={**a[1], "Idempotency-Key": "x" * 300})
    (deleted,) = events(client, a[1], type="customer.deleted")
    assert deleted["request"]["idempotency_key"] is None


def test_noop_update_emits_nothing(client, a):
    cus = client.post("/v1/customers", content="name=A", headers={**a[1], **FORM}).json()["id"]
    client.post(f"/v1/customers/{cus}", content="name=A", headers={**a[1], **FORM})
    client.post(f"/v1/customers/{cus}", headers=a[1])
    assert events(client, a[1], type="customer.updated") == []


def test_other_tenant_event_cursor_is_404(client, a, b):
    client.post("/v1/customers", headers=a[1])
    theirs = events(client, a[1])[0]["id"]
    r = client.get(f"/v1/events?starting_after={theirs}", headers=b[1])
    assert r.status_code == 404
