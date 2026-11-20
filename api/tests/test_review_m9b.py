"""Regression tests for the M9b confirm/charges review."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app import charges, ledger, seed
from app.ledger_check import ledger_check
from app.models import Charge, JournalEntry, LedgerAccount

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
API_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
def a(db):
    m, sk, _ = seed.seed(db, "r9ba@example.com")
    return m, {"Authorization": f"Bearer {sk}", **FORM}


@pytest.fixture
def b(db):
    m, sk, _ = seed.seed(db, "r9bb@example.com")
    return m, {"Authorization": f"Bearer {sk}", **FORM}


@pytest.fixture(autouse=True)
def _reconciles(db):
    yield
    assert ledger_check(db, own_txn=False) == []


def post(client, h, path, body=""):
    return client.post(path, content=body, headers=h)


def new_pi(client, h, extra=""):
    return post(client, h, "/v1/payment_intents", "amount=1000&currency=usd" + extra).json()


def event_types(client, h):
    return [e["type"] for e in reversed(client.get("/v1/events", headers=h).json()["data"])]


def test_other_merchant_cannot_confirm(client, a, b):
    pi = new_pi(client, a[1])
    r = post(client, b[1], f"/v1/payment_intents/{pi['id']}/confirm", "payment_method=pm_card_visa")
    assert r.status_code == 404


def test_confirm_with_other_merchants_pm_is_404(client, db, a, b):
    theirs = post(
        client, a[1], "/v1/payment_intents", "amount=1000&currency=usd&payment_method=pm_card_visa"
    ).json()["payment_method"]
    pi = new_pi(client, b[1])
    r = post(client, b[1], f"/v1/payment_intents/{pi['id']}/confirm", f"payment_method={theirs}")
    assert r.status_code == 404 and r.json()["error"]["param"] == "payment_method"


def test_confirm_from_requires_capture_is_unexpected_state(client, a):
    pi = new_pi(client, a[1], "&capture_method=manual&payment_method=pm_card_visa&confirm=true")
    assert pi["status"] == "requires_capture"
    r = post(client, a[1], f"/v1/payment_intents/{pi['id']}/confirm")
    assert r.json()["error"]["code"] == "payment_intent_unexpected_state"


def test_reconfirm_from_requires_action_with_new_card(client, a):
    pi = new_pi(
        client,
        a[1],
        "&payment_method=pm_card_authenticationRequired&confirm=true"
        "&return_url=https://example.com/r",
    )
    assert pi["status"] == "requires_action"
    r = post(client, a[1], f"/v1/payment_intents/{pi['id']}/confirm", "payment_method=pm_card_visa")
    assert r.json()["status"] == "succeeded" and r.json()["next_action"] is None


def test_next_action_uses_public_base_url(client, a):
    pi = new_pi(
        client,
        a[1],
        "&payment_method=pm_card_authenticationRequired&confirm=true"
        "&return_url=https://example.com/r",
    )
    assert pi["next_action"]["redirect_to_url"]["url"].startswith(
        "https://testserver/checkout/3ds/"
    )


def test_create_confirm_decline_persists_pi_and_orders_events(client, a):
    r = post(
        client,
        a[1],
        "/v1/payment_intents",
        "amount=1000&currency=usd&payment_method=pm_card_visa_chargeDeclined&confirm=true",
    )
    assert r.status_code == 402
    pi_id = r.json()["error"]["payment_intent"]["id"]
    assert client.get(f"/v1/payment_intents/{pi_id}", headers=a[1]).status_code == 200
    assert event_types(client, a[1]) == [
        "payment_intent.created",
        "charge.failed",
        "payment_intent.payment_failed",
    ]


def test_create_confirm_without_pm_persists_nothing(client, a):
    r = post(client, a[1], "/v1/payment_intents", "amount=1000&currency=usd&confirm=true")
    assert r.status_code == 400 and r.json()["error"]["param"] == "payment_method"
    assert client.get("/v1/payment_intents", headers=a[1]).json()["data"] == []
    assert event_types(client, a[1]) == []


def test_confirm_invalid_return_url(client, a):
    pi = new_pi(client, a[1], "&payment_method=pm_card_visa")
    r = post(client, a[1], f"/v1/payment_intents/{pi['id']}/confirm", "return_url=javascript:x")
    assert r.json()["error"]["code"] == "url_invalid"


def test_test_pm_id_on_update(client, a):
    pi = new_pi(client, a[1])
    r = post(client, a[1], f"/v1/payment_intents/{pi['id']}", "payment_method=pm_card_visa")
    assert r.json()["status"] == "requires_confirmation"
    assert (
        r.json()["payment_method"].startswith("pm_")
        and r.json()["payment_method"] != "pm_card_visa"
    )


def test_capture_posts_the_designed_legs(client, db, a):
    pi = new_pi(client, a[1], "&payment_method=pm_card_visa&confirm=true")
    ch = db.get(Charge, pi["latest_charge"])
    bt_jtx = db.execute(
        text("SELECT journal_transaction_id FROM balance_transactions WHERE id = :b"),
        {"b": ch.balance_transaction_id},
    ).scalar()
    legs = dict(
        db.execute(
            select(LedgerAccount.kind, JournalEntry.amount)
            .join(LedgerAccount, LedgerAccount.id == JournalEntry.account_id)
            .where(JournalEntry.transaction_id == bt_jtx)
        ).all()
    )
    fee = ledger.fee(1000)
    assert legs == {
        "card_network_clearing": 1000,
        "merchant_pending": -(1000 - fee),
        "platform_fees": -fee,
    }


def test_capture_charge_refuses_a_second_capture(client, db, a):
    pi = new_pi(client, a[1], "&payment_method=pm_card_visa&confirm=true")
    ch = db.get(Charge, pi["latest_charge"])
    with pytest.raises(ValueError):
        charges.capture_charge(db, ch, 1000)


def test_database_rejects_a_second_capture_posting(client, db, a):
    pi = new_pi(client, a[1], "&payment_method=pm_card_visa&confirm=true")
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            ledger.post_capture(db, a[0].id, pi["latest_charge"], 1000)


def test_database_rejects_a_second_succeeded_charge(client, db, a):
    pi = new_pi(client, a[1], "&payment_method=pm_card_visa&confirm=true")
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(
                text(
                    "INSERT INTO charges (id, merchant_id, payment_intent_id, payment_method_id, "
                    "amount, currency, amount_captured, amount_refunded, captured, status, "
                    "created) "
                    "SELECT 'ch_dup', merchant_id, payment_intent_id, payment_method_id, amount, "
                    "currency, 0, 0, false, 'succeeded', now() FROM charges WHERE id = :c"
                ),
                {"c": pi["latest_charge"]},
            )


@pytest.mark.parametrize("url", ["", "http://example.com", "ftp://x"])
def test_app_refuses_to_start_without_a_usable_public_base_url(url):
    env = {**os.environ, "PUBLIC_BASE_URL": url}
    env.pop("ALLOW_INSECURE_URLS", None)
    proc = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0 and "PUBLIC_BASE_URL" in proc.stderr
