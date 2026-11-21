# ruff: noqa: F811  (pytest fixtures shared from test_dashboard_auth)
"""Backend for the redesigned UI: the landing page's demo checkout and the payment detail's
timeline (events plus the ledger postings behind them)."""

from datetime import UTC, datetime

from app.ledger_check import ledger_check
from app.routers import checkout as checkout_router
from tests.test_dashboard_auth import (  # noqa: F401
    CSRF,
    _reset_ratelimit,
    c,
    make_client,
    signed_up,
)

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
EXP_YEAR = str(datetime.now(UTC).year + 5)


def card(number="4242424242424242"):
    return {
        "card[number]": number,
        "card[exp_month]": "12",
        "card[exp_year]": EXP_YEAR,
        "card[cvc]": "123",
    }


# --- demo checkout --------------------------------------------------------------------------


def test_demo_checkout_creates_a_payable_30_dollar_session(c, db):
    r = c.post("/checkout-api/demo", headers=CSRF)
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert url.startswith("https://testserver/checkout/cs_")
    cs_id = url.rsplit("/", 1)[1]
    view = c.get(f"/checkout-api/sessions/{cs_id}").json()
    assert view["amount_total"] == 3000 and view["status"] == "open"
    assert [i["name"] for i in view["line_items"]] == ["Field notebook", "Brass pen"]
    paid = c.post(f"/checkout-api/sessions/{cs_id}/pay", data=card())
    assert paid.status_code == 200, paid.text
    assert paid.json()["redirect"] == f"https://testserver/demo/thanks?session={cs_id}"
    assert ledger_check(db, own_txn=False) == []


def test_demo_checkouts_share_one_demo_store(c, db):
    a = c.post("/checkout-api/demo", headers=CSRF).json()["url"].rsplit("/", 1)[1]
    b = c.post("/checkout-api/demo", headers=CSRF).json()["url"].rsplit("/", 1)[1]
    from app.models import CheckoutSession

    assert db.get(CheckoutSession, a).merchant_id == db.get(CheckoutSession, b).merchant_id


def test_demo_checkout_needs_the_dashboard_header(c):
    assert c.post("/checkout-api/demo").status_code == 403


def test_demo_checkout_is_rate_limited(c, monkeypatch):
    monkeypatch.setattr(checkout_router, "DEMO_PER_MINUTE", 1)
    assert c.post("/checkout-api/demo", headers=CSRF).status_code == 200
    r = c.post("/checkout-api/demo", headers=CSRF)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limit"


# --- payment timeline -----------------------------------------------------------------------


def sk_post(c, keys, path, **data):
    r = c.post(path, data=data, headers={"Authorization": f"Bearer {keys['secret_key']}"})
    assert r.status_code == 200, r.text
    return r.json()


def test_timeline_lists_events_and_balanced_postings(c, db):
    keys = signed_up(c, "timeline@x.com")
    pi = sk_post(
        c,
        keys,
        "/v1/payment_intents",
        amount="2000",
        currency="usd",
        capture_method="manual",
        payment_method="pm_card_visa",
        confirm="true",
    )
    sk_post(c, keys, f"/v1/payment_intents/{pi['id']}/capture")
    sk_post(c, keys, "/v1/refunds", payment_intent=pi["id"], amount="500")
    r = c.get(f"/dashboard-api/payment_intents/{pi['id']}/timeline")
    assert r.status_code == 200, r.text
    body = r.json()
    types = [e["type"] for e in body["events"]]
    for expected in (
        "payment_intent.created",
        "charge.succeeded",
        "charge.captured",
        "payment_intent.succeeded",
        "refund.created",
        "charge.refunded",
    ):
        assert expected in types
    assert [p["type"] for p in body["postings"]] == ["charge", "refund"]
    for posting in body["postings"]:
        assert sum(e["amount"] for e in posting["entries"]) == 0
    capture = body["postings"][0]
    assert {e["account"] for e in capture["entries"]} == {
        "card_network_clearing",
        "merchant_pending",
        "platform_fees",
    }
    assert ledger_check(db, own_txn=False) == []


def test_timeline_is_tenant_scoped_and_cookie_only(make_client, db):
    a, b = make_client(), make_client()
    keys = signed_up(a, "owner@x.com")
    signed_up(b, "other@x.com")
    pi = sk_post(
        a,
        keys,
        "/v1/payment_intents",
        amount="900",
        currency="usd",
        payment_method="pm_card_visa",
        confirm="true",
    )
    assert b.get(f"/dashboard-api/payment_intents/{pi['id']}/timeline").status_code == 404
    a.cookies.clear()  # an API key alone can't read dashboard-api
    r = a.get(
        f"/dashboard-api/payment_intents/{pi['id']}/timeline",
        headers={"Authorization": f"Bearer {keys['secret_key']}"},
    )
    assert r.status_code == 401
