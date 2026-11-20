"""Regression tests for the M3b review findings."""

import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import errors, forms, seed
from app.db import SessionLocal
from app.ids import new_id
from app.main import app
from app.models import Customer
from app.tenancy import paginate

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@pytest.fixture
def a(db):
    merchant, sk, _ = seed.seed(db, "ra@example.com")
    return merchant, {"Authorization": f"Bearer {sk}"}


@pytest.fixture
def b(db):
    merchant, sk, _ = seed.seed(db, "rb@example.com")
    return merchant, {"Authorization": f"Bearer {sk}"}


def make(client, headers, body=""):
    r = client.post("/v1/customers", content=body, headers={**headers, **FORM})
    assert r.status_code == 200, r.text
    return r.json()


def test_paginate_scopes_to_merchant_even_if_stmt_does_not(client, db, a, b):
    make(client, a[1])
    mine = make(client, b[1])
    page = paginate(db, select(Customer), Customer, b[0].id, forms.ListParams(), "/x")
    assert [c.id for c in page["data"]] == [mine["id"]]


def test_validate_raises_api_error():
    with pytest.raises(errors.ApiError) as exc:
        forms.validate(forms.ListParams, {"limit": "0"})
    assert exc.value.status == 400 and exc.value.param == "limit"


def test_non_id_shaped_value_is_not_echoed(client, a):
    r = client.get("/v1/customers/4242424242424242", headers=a[1])
    assert r.status_code == 404 and "4242" not in r.text


def test_id_shaped_value_is_echoed(client, a):
    r = client.get("/v1/customers/cus_doesnotexist", headers=a[1])
    assert "cus_doesnotexist" in r.json()["error"]["message"]


@pytest.mark.parametrize(
    "qs", ["starting_after=", "ending_before=", "starting_after=&ending_before="]
)
def test_empty_cursors_are_ignored(client, a, qs):
    make(client, a[1])
    r = client.get(f"/v1/customers?{qs}", headers=a[1])
    assert r.status_code == 200 and len(r.json()["data"]) == 1


def test_update_removals_do_not_count_against_the_cap(client, a):
    c = make(client, a[1], "&".join(f"metadata[k{i}]=v" for i in range(40)))
    removes = [f"metadata[k{i}]=" for i in range(30)]
    adds = [f"metadata[n{i}]=v" for i in range(30)]
    r = client.post(
        f"/v1/customers/{c['id']}", content="&".join(removes + adds), headers={**a[1], **FORM}
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["metadata"]) == 40


def test_long_metadata_key_param_is_truncated(client, a):
    r = client.post(
        "/v1/customers", content="metadata[" + "k" * 5000 + "]=v", headers={**a[1], **FORM}
    )
    assert r.status_code == 400 and len(r.json()["error"]["param"]) <= 100


@pytest.mark.parametrize("method", ["get", "delete"])
def test_retrieve_and_delete_reject_unknown_params(client, a, method):
    c = make(client, a[1])
    r = getattr(client, method)(f"/v1/customers/{c['id']}?bogus=1", headers=a[1])
    assert r.status_code == 400 and r.json()["error"]["code"] == "parameter_unknown"


def test_empty_email_filter_means_no_filter(client, a):
    make(client, a[1], "email=x@example.com")
    make(client, a[1])
    assert len(client.get("/v1/customers?email=", headers=a[1]).json()["data"]) == 2


def test_other_tenant_ending_before_is_404(client, a, b):
    theirs = make(client, a[1])
    r = client.get(f"/v1/customers?ending_before={theirs['id']}", headers=b[1])
    assert r.status_code == 404 and r.json()["error"]["param"] == "ending_before"


def test_other_tenant_email_filter_finds_nothing(client, a, b):
    make(client, a[1], "email=secret@example.com")
    r = client.get("/v1/customers?email=secret@example.com", headers=b[1])
    assert r.json()["data"] == []


def test_concurrent_metadata_updates_do_not_lose_writes(engine):
    """Two updates queue behind a row lock; with FOR UPDATE both keys survive.

    Without the lock both would read the old metadata and the second write would win.
    Uses real commits (not the savepoint fixture), cleaned up at the end."""
    with SessionLocal.begin() as s:
        merchant, sk, _ = seed.seed(s, f"{new_id('conc_')}@example.com")
        merchant_id = merchant.id
    headers = {"Authorization": f"Bearer {sk}", **FORM}
    try:
        with TestClient(app) as client:
            cid = client.post("/v1/customers", headers=headers).json()["id"]
        results = []

        def update(key):
            with TestClient(app) as c:
                r = c.post(f"/v1/customers/{cid}", content=f"metadata[{key}]=1", headers=headers)
                results.append(r.status_code)

        with engine.connect() as locker:
            tx = locker.begin()
            locker.execute(text("SELECT 1 FROM customers WHERE id = :id FOR UPDATE"), {"id": cid})
            threads = [threading.Thread(target=update, args=(k,)) for k in "ab"]
            for t in threads:
                t.start()
            time.sleep(0.5)  # both requests are now blocked on the lock
            tx.rollback()
        for t in threads:
            t.join(10)
        assert results == [200, 200]
        with TestClient(app) as client:
            assert client.get(f"/v1/customers/{cid}", headers=headers).json()["metadata"] == {
                "a": "1",
                "b": "1",
            }
    finally:
        with engine.begin() as conn:
            for table in ("customers", "api_keys"):
                conn.execute(
                    text(f"DELETE FROM {table} WHERE merchant_id = :m"), {"m": merchant_id}
                )
            conn.execute(text("DELETE FROM merchants WHERE id = :m"), {"m": merchant_id})
