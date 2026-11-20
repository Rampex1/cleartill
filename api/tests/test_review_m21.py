# ruff: noqa: F811  (pytest fixtures shared from test_sdk_integration)
"""M21 review: SDK round trips the first integration suite didn't cover."""

import cleartill
import pytest

from app import urls
from tests.test_sdk_integration import ct, keys, ledger_clean  # noqa: F401


def test_metadata_unset_one_key_and_clear_all(ct):
    c = ct.customers.create(metadata={"a": "1", "b": "2"})
    assert ct.customers.update(c["id"], metadata={"a": ""})["metadata"] == {"b": "2"}
    assert ct.customers.update(c["id"], metadata={})["metadata"] == {}


def test_delete_customer_and_endpoint_and_boolean_update(ct, monkeypatch):
    monkeypatch.setattr(urls, "_resolve", lambda host, port: ["93.184.216.34"])
    c = ct.customers.create()
    assert ct.customers.delete(c["id"])["deleted"] is True
    we = ct.webhook_endpoints.create(url="https://example.com/h", enabled_events=["*"])
    assert ct.webhook_endpoints.update(we["id"], disabled=True)["status"] == "disabled"
    assert ct.webhook_endpoints.delete(we["id"])["deleted"] is True


def test_empty_enabled_events_gets_the_servers_error(ct, monkeypatch):
    monkeypatch.setattr(urls, "_resolve", lambda host, port: ["93.184.216.34"])
    with pytest.raises(cleartill.InvalidRequestError):
        ct.webhook_endpoints.create(url="https://example.com/h", enabled_events=[])


def test_payment_intent_update_cancel_and_event_lists(ct):
    pi = ct.payment_intents.create(amount=2000, currency="usd")
    pi = ct.payment_intents.update(pi["id"], amount=2500, metadata={"order": "7"})
    assert (pi["amount"], pi["metadata"]) == (2500, {"order": "7"})
    pi = ct.payment_intents.confirm(pi["id"], payment_method="pm_card_visa")
    assert pi["status"] == "succeeded"
    other = ct.payment_intents.create(amount=900, currency="usd")
    assert ct.payment_intents.cancel(other["id"])["status"] == "canceled"
    types = {e["type"] for e in ct.events.list_all(limit=5)}
    assert {
        "payment_intent.created",
        "payment_intent.succeeded",
        "payment_intent.canceled",
    } <= types
    assert [b["type"] for b in ct.balance_transactions.list_all()] == ["charge"]
    latest = ct.payment_intents.retrieve(pi["id"], expand=["latest_charge"])
    assert latest["latest_charge"]["object"] == "charge"
