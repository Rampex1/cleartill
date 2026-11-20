"""M21: the cleartill Python SDK against the real app (TestClient as its http_client)."""

import json
import time
import uuid
from datetime import UTC, datetime

import cleartill
import pytest

from app import ledger, seed, urls, webhooks
from app.ledger_check import ledger_check

BASE = "https://testserver"
EXP_YEAR = datetime.now(UTC).year + 5


@pytest.fixture(autouse=True)
def ledger_clean(db):
    yield
    assert ledger_check(db, own_txn=False) == []


@pytest.fixture
def keys(db):
    return seed.seed(db, "sdk@example.com")


def sdk(client, key):
    c = cleartill.Client(key, base_url=BASE, http_client=client)
    c._sleep = lambda s: None
    return c


@pytest.fixture
def ct(client, keys):
    return sdk(client, keys[1])


@pytest.fixture
def ct_pk(client, keys):
    return sdk(client, keys[2])


def test_customer_create_retrieve_update(ct):
    cus = ct.customers.create(email="a@example.com", name="Ann", metadata={"tier": "gold"})
    assert cus["id"].startswith("cus_")
    assert cus["metadata"] == {"tier": "gold"}

    got = ct.customers.retrieve(cus["id"])
    assert got["id"] == cus["id"]
    assert got["email"] == "a@example.com"
    assert got["metadata"] == {"tier": "gold"}

    upd = ct.customers.update(cus["id"], name="Ann B", metadata={"extra": "1"})
    assert upd["name"] == "Ann B"
    assert upd["metadata"]["extra"] == "1"
    assert ct.customers.retrieve(cus["id"])["name"] == "Ann B"


def test_pi_capture_refund_flow(ct):
    pi = ct.payment_intents.create(
        amount=2000,
        currency="usd",
        payment_method="pm_card_visa",
        confirm=True,
        capture_method="manual",
    )
    assert pi["status"] == "requires_capture"

    captured = ct.payment_intents.capture(pi["id"], amount_to_capture=1500)
    assert captured["status"] == "succeeded"
    assert captured["amount_received"] == 1500

    refund = ct.refunds.create(payment_intent=pi["id"], amount=500)
    assert refund["id"].startswith("re_")
    assert refund["amount"] == 500

    charge = ct.charges.retrieve(captured["latest_charge"])
    assert charge["amount_refunded"] == 500
    assert charge["amount_captured"] == 1500

    balance = ct.balance.retrieve()
    assert balance["pending"] == [{"amount": 1500 - ledger.fee(1500), "currency": "usd"}]
    assert balance["available"] == [{"amount": -500, "currency": "usd"}]


def test_declined_pi_raises_card_error(ct):
    with pytest.raises(cleartill.CardError) as e:
        ct.payment_intents.create(
            amount=1000, currency="usd", payment_method="pm_card_visa_chargeDeclined", confirm=True
        )
    assert e.value.code == "card_declined"
    assert e.value.decline_code == "generic_decline"
    assert e.value.http_status == 402
    assert e.value.request_id
    assert e.value.json_body["error"]["type"] == "card_error"


def card():
    return {
        "number": "4242424242424242",
        "exp_month": "12",
        "exp_year": str(EXP_YEAR),
        "cvc": "123",
    }


def test_payment_method_create_with_secret_key_is_permission_error(ct):
    with pytest.raises(cleartill.PermissionError) as e:
        ct.payment_methods.create(type="card", card=card())
    assert e.value.http_status == 403
    assert "4242" not in str(e.value)


def test_payment_method_create_with_publishable_key(ct_pk, ct):
    pm = ct_pk.payment_methods.create(type="card", card=card())
    assert pm["id"].startswith("pm_")
    assert pm["card"]["last4"] == "4242"
    assert ct.payment_methods.retrieve(pm["id"])["id"] == pm["id"]


def test_missing_customer_is_invalid_request_404(ct):
    with pytest.raises(cleartill.InvalidRequestError) as e:
        ct.customers.retrieve("cus_doesnotexist")
    assert e.value.http_status == 404
    assert e.value.code == "resource_missing"


def test_bad_key_is_authentication_error(client, keys):
    with pytest.raises(cleartill.AuthenticationError) as e:
        sdk(client, "sk_test_" + "x" * 24).customers.list()
    assert e.value.http_status == 401


def test_same_idempotency_key_same_params_replays(ct):
    key = str(uuid.uuid4())
    a = ct.customers.create(email="idem@example.com", idempotency_key=key)
    b = ct.customers.create(email="idem@example.com", idempotency_key=key)
    assert a["id"] == b["id"]
    assert len(list(ct.customers.list_all())) == 1


def test_same_idempotency_key_different_params_is_idempotency_error(ct):
    key = str(uuid.uuid4())
    ct.customers.create(email="one@example.com", idempotency_key=key)
    with pytest.raises(cleartill.IdempotencyError) as e:
        ct.customers.create(email="two@example.com", idempotency_key=key)
    assert e.value.http_status == 400


def test_list_all_pages_through_25_customers(ct):
    created = {ct.customers.create(email=f"c{i}@example.com")["id"] for i in range(25)}
    ids = [c["id"] for c in ct.customers.list_all(limit=10)]
    assert len(ids) == 25
    assert set(ids) == created


def test_list_returns_page(ct):
    for i in range(3):
        ct.customers.create(email=f"p{i}@example.com")
    page = ct.customers.list(limit=2)
    assert page["object"] == "list"
    assert len(page["data"]) == 2
    assert page["has_more"] is True


def test_checkout_session_create(ct):
    cs = ct.checkout.sessions.create(
        mode="payment",
        success_url="https://example.com/ok",
        line_items=[
            {
                "price_data": {
                    "currency": "usd",
                    "unit_amount": 1500,
                    "product_data": {"name": "Tee"},
                },
                "quantity": 2,
            }
        ],
    )
    assert cs["id"].startswith("cs_")
    assert ct.checkout.sessions.retrieve(cs["id"])["id"] == cs["id"]


def test_webhook_endpoint_secret_verifies_server_signature(ct, monkeypatch):
    monkeypatch.setattr(urls, "_resolve", lambda host, port: ["93.184.216.34"])
    we = ct.webhook_endpoints.create(url="https://example.com/h", enabled_events=["*"])
    secret = we["secret"]
    assert secret.startswith("whsec_")
    assert we["enabled_events"] == ["*"]

    payload = json.dumps({"id": "evt_1", "object": "event", "type": "customer.created"})
    t = int(time.time())
    header = webhooks.sign(secret, t, payload)
    event = cleartill.Webhook.construct_event(payload, header, secret)
    assert event["id"] == "evt_1"
    event = cleartill.Webhook.construct_event(payload.encode(), header, secret)
    assert event["type"] == "customer.created"

    with pytest.raises(cleartill.SignatureVerificationError):
        cleartill.Webhook.construct_event(payload, webhooks.sign("whsec_other", t, payload), secret)
