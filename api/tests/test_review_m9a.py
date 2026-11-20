"""Regression tests for the M9a PaymentIntent review."""

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import seed
from app.db import SessionLocal
from app.ids import new_id
from app.ledger_check import ledger_check
from app.main import app

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@pytest.fixture
def a(db):
    merchant, sk, _ = seed.seed(db, "r9a@example.com")
    return merchant, {"Authorization": f"Bearer {sk}", **FORM}


@pytest.fixture
def b(db):
    merchant, sk, _ = seed.seed(db, "r9b@example.com")
    return merchant, {"Authorization": f"Bearer {sk}", **FORM}


@pytest.fixture(autouse=True)
def _reconciles(db):
    yield
    assert ledger_check(db, own_txn=False) == []


def create(client, h, body="amount=1000&currency=usd"):
    return client.post("/v1/payment_intents", content=body, headers=h)


@pytest.mark.parametrize(
    ("amount", "code"), [("49", "amount_too_small"), ("100000000", "amount_too_large")]
)
def test_amount_bounds_use_stripe_codes(client, a, amount, code):
    r = create(client, a[1], f"amount={amount}&currency=usd")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == code and r.json()["error"]["param"] == "amount"


@pytest.mark.parametrize("amount", ["1_000", " 100", "+100", "1000.0", "１０００"])
def test_amount_must_be_plain_ascii_digits(client, a, amount):
    r = create(client, a[1], f"amount={amount}&currency=usd")
    assert r.status_code == 400 and r.json()["error"]["param"] == "amount"


def test_update_amount_too_small_code(client, a):
    pi = create(client, a[1]).json()
    r = client.post(f"/v1/payment_intents/{pi['id']}", content="amount=49", headers=a[1])
    assert r.json()["error"]["code"] == "amount_too_small"


def test_empty_return_url_is_null(client, a):
    r = create(client, a[1], "amount=1000&currency=usd&return_url=")
    assert r.status_code == 200 and r.json()["return_url"] is None


def test_empty_cancellation_reason_means_none(client, a):
    pi = create(client, a[1]).json()
    r = client.post(
        f"/v1/payment_intents/{pi['id']}/cancel", content="cancellation_reason=", headers=a[1]
    )
    assert r.status_code == 200 and r.json()["cancellation_reason"] is None


def test_bad_capture_method_names_the_param(client, a):
    r = create(client, a[1], "amount=1000&currency=usd&capture_method=later")
    assert r.json()["error"]["param"] == "capture_method"


def test_retrieve_rejects_unknown_params(client, a):
    pi = create(client, a[1]).json()
    r = client.get(f"/v1/payment_intents/{pi['id']}?foo=1", headers=a[1])
    assert r.status_code == 400


def test_idempotent_create_replays_with_one_event(client, a):
    h = {**a[1], "Idempotency-Key": "pi-1"}
    first = create(client, h)
    second = create(client, h)
    assert first.json()["id"] == second.json()["id"]
    assert second.headers["idempotent-replayed"] == "true"
    events = client.get("/v1/events?type=payment_intent.created", headers=a[1]).json()["data"]
    assert len(events) == 1


def test_other_tenant_customer_filter_returns_nothing(client, a, b):
    cus = client.post("/v1/customers", headers=a[1]).json()["id"]
    create(client, a[1], f"amount=1000&currency=usd&customer={cus}")
    r = client.get(f"/v1/payment_intents?customer={cus}", headers=b[1])
    assert r.status_code == 200 and r.json()["data"] == []


def test_other_tenant_cursor_is_404(client, a, b):
    pi = create(client, a[1]).json()
    r = client.get(f"/v1/payment_intents?starting_after={pi['id']}", headers=b[1])
    assert r.status_code == 404 and r.json()["error"]["param"] == "starting_after"


def test_concurrent_cancels_cancel_once(engine):
    """Committed data, two threads: exactly one cancel wins; one canceled event."""
    with SessionLocal.begin() as s:
        m, sk, _ = seed.seed(s, f"{new_id('r9c_')}@example.com")
        mid = m.id
    h = {"Authorization": f"Bearer {sk}", **FORM}
    try:
        with TestClient(app) as c:
            pi = create(c, h).json()["id"]
        barrier = threading.Barrier(2)
        codes = []

        def cancel():
            with TestClient(app) as c:
                barrier.wait()
                codes.append(c.post(f"/v1/payment_intents/{pi}/cancel", headers=h).status_code)

        threads = [threading.Thread(target=cancel) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert sorted(codes) == [200, 400]
        with engine.connect() as conn:
            n = conn.execute(
                text(
                    "SELECT count(*) FROM events WHERE merchant_id = :m "
                    "AND type = 'payment_intent.canceled'"
                ),
                {"m": mid},
            ).scalar()
        assert n == 1
    finally:
        with engine.begin() as conn:
            for table in ("events", "payment_intents", "idempotency_keys", "api_keys"):
                conn.execute(text(f"DELETE FROM {table} WHERE merchant_id = :m"), {"m": mid})
            conn.execute(text("DELETE FROM merchants WHERE id = :m"), {"m": mid})
