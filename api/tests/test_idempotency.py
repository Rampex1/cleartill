"""M4: the request executor and Stripe-style idempotency keys."""

import threading
from datetime import timedelta
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import clock, errors, executor, forms, seed
from app.auth import AuthContext, require_secret_key
from app.db import SessionLocal, get_db
from app.ids import new_id
from app.main import app
from app.models import Customer

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
REPLAYED = "Idempotent-Replayed"


@pytest.fixture
def a(db):
    merchant, sk, _ = seed.seed(db, "idem-a@example.com")
    return merchant, {"Authorization": f"Bearer {sk}", **FORM}


@pytest.fixture
def b(db):
    merchant, sk, _ = seed.seed(db, "idem-b@example.com")
    return merchant, {"Authorization": f"Bearer {sk}", **FORM}


def post(client, headers, path="/v1/customers", body="", key=None):
    extra = {"Idempotency-Key": key} if key is not None else {}
    return client.post(path, content=body, headers={**headers, **extra})


def err(r):
    return r.json()["error"]


def customer_count(db, merchant_id):
    return db.scalar(
        text("SELECT count(*) FROM customers WHERE merchant_id = :m"), {"m": merchant_id}
    )


def key_rows(db, merchant_id, key=None):
    sql = "SELECT response_status FROM idempotency_keys WHERE merchant_id = :m"
    if key is not None:
        sql += " AND key = :k"
    return db.execute(text(sql), {"m": merchant_id, "k": key}).scalars().all()


def throwaway(db, handler):
    """A TestClient on a separate app whose POST /t runs `handler(db)` through execute()."""
    t = FastAPI()
    errors.install(t)

    @t.post("/t")
    def route(
        request: Request,
        session: Annotated[Session, Depends(get_db)],
        auth: Annotated[AuthContext, Depends(require_secret_key)],
        params: Annotated[dict, Depends(forms.params)],
    ):
        return executor.execute(
            session, request, auth.merchant.id, params, lambda: handler(session, auth)
        )

    def override_get_db():
        yield db

    t.dependency_overrides[get_db] = override_get_db
    return TestClient(t, raise_server_exceptions=False)


# --- replay and key reuse ------------------------------------------------------------------


def test_without_key_identical_creates_make_two_customers(client, db, a):
    for _ in range(2):
        assert post(client, a[1], body="email=a@x.com").status_code == 200
    assert customer_count(db, a[0].id) == 2


def test_same_key_same_params_returns_identical_body(client, a):
    r1 = post(client, a[1], body="email=a@x.com", key="k1")
    r2 = post(client, a[1], body="email=a@x.com", key="k1")
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()


def test_only_the_replay_has_the_replayed_header(client, a):
    r1 = post(client, a[1], body="email=a@x.com", key="k1")
    r2 = post(client, a[1], body="email=a@x.com", key="k1")
    assert REPLAYED not in r1.headers
    assert r2.headers.get(REPLAYED) == "true"


def test_same_key_same_params_creates_one_customer(client, a):
    post(client, a[1], body="email=a@x.com", key="k1")
    post(client, a[1], body="email=a@x.com", key="k1")
    assert len(client.get("/v1/customers", headers=a[1]).json()["data"]) == 1


def test_key_row_stores_response_status(client, db, a):
    post(client, a[1], body="email=a@x.com", key="k1")
    assert key_rows(db, a[0].id, "k1") == [200]


def test_param_order_does_not_change_the_request_hash(client, a):
    r1 = post(client, a[1], body="email=a@x.com&name=N", key="k1")
    r2 = post(client, a[1], body="name=N&email=a@x.com", key="k1")
    assert r2.status_code == 200 and r2.headers.get(REPLAYED) == "true"
    assert r2.json()["id"] == r1.json()["id"]


def test_same_key_different_params_is_rejected(client, db, a):
    post(client, a[1], body="email=a@x.com", key="k1")
    r = post(client, a[1], body="email=b@x.com", key="k1")
    assert r.status_code == 400
    assert err(r)["type"] == "idempotency_error"
    assert err(r)["code"] == "idempotency_key_reused"
    assert customer_count(db, a[0].id) == 1


def test_same_key_on_a_different_path_is_rejected(client, a):
    cid = post(client, a[1], body="name=N", key="k1").json()["id"]
    r = post(client, a[1], path=f"/v1/customers/{cid}", body="name=N", key="k1")
    assert r.status_code == 400 and err(r)["type"] == "idempotency_error"


def test_keys_are_scoped_per_merchant(client, a, b):
    ra = post(client, a[1], body="email=a@x.com", key="shared")
    rb = post(client, b[1], body="email=a@x.com", key="shared")
    assert rb.status_code == 200 and REPLAYED not in rb.headers
    assert rb.json()["id"] != ra.json()["id"]
    assert [c["id"] for c in client.get("/v1/customers", headers=b[1]).json()["data"]] == [
        rb.json()["id"]
    ]


# --- failures free the key -----------------------------------------------------------------


def test_failed_request_does_not_store_the_key(client, db, a):
    bad = "email=" + "x" * 513
    r1 = post(client, a[1], body=bad, key="k1")
    assert r1.status_code == 400
    assert key_rows(db, a[0].id, "k1") == []
    r2 = post(client, a[1], body=bad, key="k1")
    assert r2.status_code == 400 and REPLAYED not in r2.headers
    assert key_rows(db, a[0].id, "k1") == []
    r3 = post(client, a[1], body="email=a@x.com", key="k1")
    assert r3.status_code == 200 and REPLAYED not in r3.headers


def test_unhandled_exception_is_500_and_retry_runs_again(db, a):
    calls = []

    def handler(session, auth):
        calls.append(1)
        raise RuntimeError("boom")

    with throwaway(db, handler) as t:
        assert post(t, a[1], path="/t", key="k1").status_code == 500
        assert post(t, a[1], path="/t", key="k1").status_code == 500
    assert len(calls) == 2


def test_result_with_error_status_is_stored_and_replayed(db, a):
    body = {"error": {"type": "card_error", "message": "Declined", "code": "card_declined"}}
    calls = []

    def handler(session, auth):
        calls.append(1)
        return executor.Result(402, body)

    with throwaway(db, handler) as t:
        r1 = post(t, a[1], path="/t", key="k1")
        r2 = post(t, a[1], path="/t", key="k1")
    assert r1.status_code == 402 and r1.json() == body and REPLAYED not in r1.headers
    assert r2.status_code == 402 and r2.json() == body and r2.headers.get(REPLAYED) == "true"
    assert len(calls) == 1


def test_api_error_rolls_back_handler_writes_and_key(db, a):
    def handler(session, auth):
        session.add(Customer(id=new_id("cus_"), merchant_id=auth.merchant.id, metadata_={}))
        session.flush()
        raise errors.ApiError(400, "invalid_request_error", "nope")

    with throwaway(db, handler) as t:
        r = post(t, a[1], path="/t", key="k1")
    assert r.status_code == 400 and err(r)["message"] == "nope"
    assert customer_count(db, a[0].id) == 0
    assert key_rows(db, a[0].id) == []


# --- key validation ------------------------------------------------------------------------


def test_key_longer_than_255_is_invalid(client, a):
    r = post(client, a[1], key="k" * 256)
    assert r.status_code == 400
    assert err(r)["type"] == "invalid_request_error"
    assert err(r)["code"] == "idempotency_key_invalid"


def test_empty_key_is_invalid(client, a):
    r = post(client, a[1], key="")
    assert r.status_code == 400 and err(r)["code"] == "idempotency_key_invalid"


def test_key_of_255_is_accepted(client, db, a):
    assert post(client, a[1], key="k" * 255).status_code == 200
    assert key_rows(db, a[0].id, "k" * 255) == [200]


# --- methods that ignore keys --------------------------------------------------------------


def test_get_with_key_stores_nothing(client, db, a):
    r = client.get("/v1/customers", headers={**a[1], "Idempotency-Key": "k1"})
    assert r.status_code == 200
    assert key_rows(db, a[0].id) == []


def test_delete_with_key_is_not_replayed(client, db, a):
    cid = post(client, a[1]).json()["id"]
    h = {**a[1], "Idempotency-Key": "k1"}
    assert client.delete(f"/v1/customers/{cid}", headers=h).status_code == 200
    r = client.delete(f"/v1/customers/{cid}", headers=h)
    assert r.status_code == 404 and REPLAYED not in r.headers
    assert key_rows(db, a[0].id) == []


# --- replay content ------------------------------------------------------------------------


def test_replay_returns_the_stored_body_not_current_state(client, a):
    cid = post(client, a[1]).json()["id"]
    path = f"/v1/customers/{cid}"
    post(client, a[1], path=path, body="name=x", key="k1")
    post(client, a[1], path=path, body="name=y")
    r = post(client, a[1], path=path, body="name=x", key="k1")
    assert r.headers.get(REPLAYED) == "true" and r.json()["name"] == "x"
    assert client.get(path, headers=a[1]).json()["name"] == "y"


# --- purging -------------------------------------------------------------------------------


def test_purge_deletes_keys_older_than_24_hours(client, db, a, monkeypatch):
    old = clock.now() - timedelta(hours=25)
    with monkeypatch.context() as m:
        m.setattr(clock, "now", lambda: old)
        post(client, a[1], key="old1")
        post(client, a[1], key="old2")
    post(client, a[1], key="new")
    assert executor.purge_idempotency_keys(db) == 2
    assert len(key_rows(db, a[0].id)) == 1


def test_key_purged_between_lookup_and_claim_still_replays(client, db, a, monkeypatch):
    r1 = post(client, a[1], body="email=a@x.com", key="k1")
    original = executor._load_key
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        return None if len(calls) == 1 else original(*args, **kwargs)

    monkeypatch.setattr(executor, "_load_key", flaky)
    r2 = post(client, a[1], body="email=a@x.com", key="k1")
    assert r2.status_code == 200 and r2.headers.get(REPLAYED) == "true"
    assert r2.json()["id"] == r1.json()["id"]
    assert customer_count(db, a[0].id) == 1


# --- concurrency (committed data) ----------------------------------------------------------


@pytest.fixture
def committed(engine):
    """A merchant committed for real (not in the savepoint fixture), removed afterwards."""
    with SessionLocal.begin() as s:
        merchant, sk, _ = seed.seed(s, f"{new_id('idem_')}@example.com")
        merchant_id = merchant.id
    yield merchant_id, {"Authorization": f"Bearer {sk}", **FORM}
    with engine.begin() as conn:
        for table in ("idempotency_keys", "customers", "api_keys"):
            conn.execute(text(f"DELETE FROM {table} WHERE merchant_id = :m"), {"m": merchant_id})
        conn.execute(text("DELETE FROM merchants WHERE id = :m"), {"m": merchant_id})


def in_thread(fn):
    """Run fn in a thread so a request that never times out fails the test instead of hanging."""
    out = []
    t = threading.Thread(target=lambda: out.append(fn()))
    t.start()
    t.join(10)
    assert out, "request did not finish within 10s (lock_timeout not applied?)"
    return out[0]


def test_concurrent_requests_with_same_key_create_one_customer(engine, committed):
    merchant_id, headers = committed
    barrier = threading.Barrier(2)
    results = []

    def send():
        with TestClient(app) as c:
            barrier.wait()
            results.append(post(c, headers, body="email=a@x.com", key="race"))

    threads = [threading.Thread(target=send) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    with engine.connect() as conn:
        assert customer_count(conn, merchant_id) == 1
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json()["id"] == results[1].json()["id"]
    assert sum(r.headers.get(REPLAYED) == "true" for r in results) == 1


def test_key_held_by_in_flight_request_is_409(engine, committed, monkeypatch):
    merchant_id, headers = committed
    monkeypatch.setattr(executor, "LOCK_TIMEOUT", "200ms")
    with engine.connect() as locker:
        tx = locker.begin()
        try:
            locker.execute(
                text(
                    "INSERT INTO idempotency_keys (merchant_id, key, request_hash, created) "
                    "VALUES (:m, 'k-held', 'x', now())"
                ),
                {"m": merchant_id},
            )
            with TestClient(app) as c:
                r = in_thread(lambda: post(c, headers, key="k-held"))
        finally:
            tx.rollback()
    assert r.status_code == 409
    assert err(r)["type"] == "idempotency_error"
    assert err(r)["code"] == "idempotency_key_in_use"


def test_business_row_lock_timeout_is_409(engine, committed, monkeypatch):
    merchant_id, headers = committed
    monkeypatch.setattr(executor, "LOCK_TIMEOUT", "200ms")
    with TestClient(app) as c:
        cid = post(c, headers).json()["id"]
        with engine.connect() as locker:
            tx = locker.begin()
            try:
                locker.execute(
                    text("SELECT 1 FROM customers WHERE id = :id FOR UPDATE"), {"id": cid}
                )
                r = in_thread(lambda: post(c, headers, path=f"/v1/customers/{cid}", body="name=z"))
            finally:
                tx.rollback()
    assert r.status_code == 409
    assert err(r)["type"] == "api_error" and err(r)["code"] == "lock_timeout"
