"""Regression tests for the M4 review: deterministic same-key overlap, commit ordering."""

import threading
import time
from datetime import datetime
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import auth, errors, executor, forms, seed
from app.db import SessionLocal, get_db
from app.ids import new_id
from app.models import Customer

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
REPLAYED = "idempotent-replayed"


def build_app(handler_factory):
    t = FastAPI()
    errors.install(t)

    @t.post("/t")
    def route(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
        ctx: Annotated[auth.AuthContext, Depends(auth.require_secret_key)],
        params: Annotated[dict, Depends(forms.params)],
    ):
        return executor.execute(db, request, ctx.merchant.id, params, handler_factory(db, ctx))

    return t


@pytest.fixture
def committed(engine):
    """A merchant committed for real (production get_db, separate connections)."""
    with SessionLocal.begin() as s:
        merchant, sk, _ = seed.seed(s, f"{new_id('m4r_')}@example.com")
        mid = merchant.id
    yield mid, {"Authorization": f"Bearer {sk}", **FORM}
    with engine.begin() as conn:
        for table in ("idempotency_keys", "customers", "api_keys"):
            conn.execute(text(f"DELETE FROM {table} WHERE merchant_id = :m"), {"m": mid})
        conn.execute(text("DELETE FROM merchants WHERE id = :m"), {"m": mid})


def _overlap(headers, first_outcome):
    """Request 1 starts its handler and blocks; request 2 (same key) must wait on the key row."""
    started, release = threading.Event(), threading.Event()
    calls = []
    results = {}

    def factory(db, ctx):
        def handler():
            calls.append(1)
            if len(calls) == 1:
                started.set()
                assert release.wait(10)
                if first_outcome == "fail":
                    raise errors.ApiError(400, "invalid_request_error", "nope")
            c = Customer(id=new_id("cus_"), merchant_id=ctx.merchant.id, metadata_={})
            db.add(c)
            db.flush()
            return {"id": c.id}

        return handler

    app = build_app(factory)

    def call(name):
        with TestClient(app, raise_server_exceptions=False) as c:
            results[name] = c.post("/t", content="x=1", headers={**headers, "Idempotency-Key": "k"})

    r1 = threading.Thread(target=call, args=("first",))
    r1.start()
    assert started.wait(10)
    r2 = threading.Thread(target=call, args=("second",))
    r2.start()
    time.sleep(0.3)
    assert r2.is_alive(), "second request should be waiting on the key row"
    release.set()
    r1.join(10)
    r2.join(10)
    return results, calls


def test_overlapping_same_key_waits_then_replays(committed, engine):
    mid, headers = committed
    results, calls = _overlap(headers, "ok")
    assert len(calls) == 1
    assert results["first"].status_code == results["second"].status_code == 200
    assert results["first"].json() == results["second"].json()
    assert REPLAYED not in results["first"].headers
    assert results["second"].headers[REPLAYED] == "true"
    with engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM customers WHERE merchant_id=:m"), {"m": mid})
        assert n.scalar() == 1


def test_overlapping_same_key_runs_again_when_first_rolls_back(committed, engine):
    mid, headers = committed
    results, calls = _overlap(headers, "fail")
    assert len(calls) == 2
    assert results["first"].status_code == 400
    assert results["second"].status_code == 200 and REPLAYED not in results["second"].headers
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT response_status FROM idempotency_keys WHERE merchant_id=:m"), {"m": mid}
        ).all()
        assert row == [(200,)]


def test_unserialisable_body_rolls_back_instead_of_committing(committed, engine):
    mid, headers = committed

    def factory(db, ctx):
        def handler():
            db.add(Customer(id=new_id("cus_"), merchant_id=ctx.merchant.id, metadata_={}))
            db.flush()
            return {"when": datetime.now()}  # not JSON-serialisable

        return handler

    with TestClient(build_app(factory), raise_server_exceptions=False) as c:
        assert c.post("/t", content="x=1", headers=headers).status_code == 500
    with engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM customers WHERE merchant_id=:m"), {"m": mid})
        assert n.scalar() == 0


def test_handler_that_commits_is_a_500_and_stores_no_key(client, db):
    _, sk, _ = seed.seed(db, "commits@example.com")
    db.commit()

    def factory(session, ctx):
        def handler():
            session.commit()
            return {"ok": True}

        return handler

    t = build_app(factory)
    t.dependency_overrides[get_db] = lambda: (yield db)
    with TestClient(t, raise_server_exceptions=False) as c:
        r = c.post(
            "/t",
            content="x=1",
            headers={"Authorization": f"Bearer {sk}", **FORM, "Idempotency-Key": "kc"},
        )
    assert r.status_code == 500
