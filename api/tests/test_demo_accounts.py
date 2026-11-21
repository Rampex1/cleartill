# ruff: noqa: F811  (pytest fixtures shared from test_dashboard_auth)
"""Demo accounts (a seeded sandbox merchant per visitor), the clock's as-of override, and the
public timeline of a paid landing-page demo checkout."""

from collections import Counter
from datetime import UTC, datetime, timedelta

from app import clock
from app.ledger_check import ledger_check
from app.routers import dashboard
from tests.test_dashboard_auth import (  # noqa: F401
    CSRF,
    _reset_ratelimit,
    c,
    make_client,
    signed_up,
)
from tests.test_ui_backend import card


def test_clock_at_overrides_now_and_restores():
    past = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    before = clock.now()
    with clock.at(past):
        assert clock.now() == past
        with clock.at(past + timedelta(days=1)):
            assert clock.now() == past + timedelta(days=1)
        assert clock.now() == past
    assert clock.now() >= before


def demo(c):
    r = c.post("/dashboard-api/demo-account", headers=CSRF)
    assert r.status_code == 200, r.text
    return r.json()["merchant"]


def test_demo_account_is_logged_in_with_two_weeks_of_consistent_history(c, db):
    merchant = demo(c)
    assert merchant["email"].endswith("@sandbox.cleartill.test")
    assert c.get("/dashboard-api/session").json()["merchant"]["id"] == merchant["id"]

    pis = c.get("/v1/payment_intents", params={"limit": 100}).json()["data"]
    statuses = Counter(pi["status"] for pi in pis)
    assert len(pis) >= 12
    for status in ("succeeded", "requires_capture", "requires_action", "requires_payment_method"):
        assert statuses[status] >= 1, statuses
    created = sorted(pi["created"] for pi in pis)
    assert created[-1] - created[0] >= 12 * 86400  # history spans about two weeks

    customers = c.get("/v1/customers", params={"limit": 100}).json()["data"]
    assert len(customers) == 6
    refunds = c.get("/v1/refunds", params={"limit": 100}).json()["data"]
    assert len(refunds) == 2
    payouts = c.get("/v1/payouts").json()["data"]
    assert sorted(p["status"] for p in payouts) == ["paid", "pending"]
    balance = c.get("/v1/balance").json()
    assert balance["available"][0]["amount"] > 0
    assert ledger_check(db, own_txn=False) == []


def test_demo_payment_timeline_shows_backdated_steps_with_postings(c, db):
    demo(c)
    pis = c.get("/v1/payment_intents", params={"limit": 100}).json()["data"]
    partial = next(pi for pi in pis if pi["amount"] == 56000)  # authorized, partly captured
    body = c.get(f"/dashboard-api/payment_intents/{partial['id']}/timeline").json()
    assert [p["type"] for p in body["postings"]][:2] == ["charge", "release"]
    assert body["events"][0]["created"] < body["postings"][0]["created"]


def test_each_click_is_a_separate_account(make_client, db):
    a, b = make_client(), make_client()
    assert demo(a)["id"] != demo(b)["id"]


def test_demo_account_needs_the_header_and_is_rate_limited(c, monkeypatch):
    assert c.post("/dashboard-api/demo-account").status_code == 403
    monkeypatch.setattr(dashboard, "DEMO_ACCOUNTS_PER_MINUTE", 1)
    demo(c)
    r = c.post("/dashboard-api/demo-account", headers=CSRF)
    assert r.status_code == 429


# --- public timeline of a paid landing-page demo --------------------------------------------


def paid_demo_session(c):
    cs_id = c.post("/checkout-api/demo", headers=CSRF).json()["url"].rsplit("/", 1)[1]
    assert c.post(f"/checkout-api/sessions/{cs_id}/pay", data=card()).status_code == 200
    return cs_id


def test_paid_demo_checkout_exposes_its_timeline(c, db):
    cs_id = paid_demo_session(c)
    r = c.get(f"/checkout-api/demo/sessions/{cs_id}/payment")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["payment"]["amount"] == 3000 and body["payment"]["status"] == "succeeded"
    assert "payment_intent.succeeded" in [e["type"] for e in body["events"]]
    assert [p["type"] for p in body["postings"]] == ["charge"]
    assert ledger_check(db, own_txn=False) == []


def test_unpaid_or_non_demo_sessions_stay_private(c, db):
    open_cs = c.post("/checkout-api/demo", headers=CSRF).json()["url"].rsplit("/", 1)[1]
    assert c.get(f"/checkout-api/demo/sessions/{open_cs}/payment").status_code == 404
    keys = signed_up(c, "merchant@x.com")
    own = c.post(
        "/v1/checkout/sessions",
        headers={"Authorization": f"Bearer {keys['secret_key']}"},
        data={
            "mode": "payment",
            "success_url": "https://shop.example/ok",
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": "1500",
            "line_items[0][price_data][product_data][name]": "Tee",
            "line_items[0][quantity]": "1",
        },
    ).json()["id"]
    c.post(f"/checkout-api/sessions/{own}/pay", data=card())
    assert c.get(f"/checkout-api/demo/sessions/{own}/payment").status_code == 404
    assert c.get("/checkout-api/demo/sessions/cs_nope/payment").status_code == 404
