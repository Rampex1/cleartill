"""M17: concurrent checkout payments (committed data; ledger rows stay behind, as CLAUDE.md
allows)."""

import threading
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi.testclient import TestClient
from sqlalchemy import text

from app import seed
from app.db import SessionLocal
from app.ids import new_id
from app.ledger_check import ledger_check
from app.main import app

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
EXP_YEAR = str(datetime.now(UTC).year + 5)
VISA = "4242424242424242"
THREE_DS = "4000002760003184"


def post(c, h, path, data):
    return c.post(path, content=urlencode(data), headers=h)


def pay(c, cs_id, number=VISA):
    data = {
        "card[number]": number,
        "card[exp_month]": "12",
        "card[exp_year]": EXP_YEAR,
        "card[cvc]": "123",
    }
    return post(c, FORM, f"/checkout-api/sessions/{cs_id}/pay", data)


def committed_session():
    with SessionLocal.begin() as s:
        _, sk, _ = seed.seed(s, f"{new_id('cko_')}@example.com")
    skh = {"Authorization": f"Bearer {sk}", **FORM}
    body = {
        "mode": "payment",
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": "1500",
        "line_items[0][price_data][product_data][name]": "Tee",
        "line_items[0][quantity]": "2",
        "success_url": "https://example.com/ok",
        "cancel_url": "https://example.com/back",
    }
    with TestClient(app) as c:
        r = post(c, skh, "/v1/checkout/sessions", body)
    assert r.status_code == 200, r.text
    return skh, r.json()


def race(*calls):
    barrier, out = threading.Barrier(len(calls)), [None] * len(calls)

    def run(i, fn):
        with TestClient(app) as c:
            barrier.wait()
            out[i] = fn(c)

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not any(t.is_alive() for t in threads), "a request hung (deadlock?)"
    return out


def succeeded_charges(pi_id):
    with SessionLocal() as s:
        return s.scalar(
            text(
                "SELECT count(*) FROM charges WHERE payment_intent_id = :p AND status = 'succeeded'"
            ),
            {"p": pi_id},
        )


def session_status(skh, cs_id):
    with TestClient(app) as c:
        r = c.get(f"/v1/checkout/sessions/{cs_id}", headers=skh)
    assert r.status_code == 200, r.text
    return r.json()["status"]


def ok_or_unexpected_state(r):
    if r.status_code == 200:
        return True
    return r.status_code == 400 and r.json()["error"].get("code") == (
        "checkout_session_unexpected_state"
    )


def test_two_simultaneous_pays_charge_once(engine):
    skh, cs = committed_session()
    out = race(lambda c: pay(c, cs["id"]), lambda c: pay(c, cs["id"]))
    assert any(r.status_code == 200 for r in out), [r.text for r in out]
    assert all(ok_or_unexpected_state(r) for r in out), [r.text for r in out]
    assert succeeded_charges(cs["payment_intent"]) == 1
    assert session_status(skh, cs["id"]) == "complete"
    assert ledger_check(engine) == []


def test_3ds_approve_racing_pay_finishes_without_deadlock(engine):
    skh, cs = committed_session()
    with TestClient(app) as c:
        r = pay(c, cs["id"], THREE_DS)
    assert r.status_code == 200, r.text
    secret = parse_qs(urlsplit(r.json()["redirect"]).query)["client_secret"][0]
    pid = cs["payment_intent"]

    def approve(c):
        return post(
            c, FORM, f"/checkout-api/3ds/{pid}", {"client_secret": secret, "result": "approve"}
        )

    out = race(approve, lambda c: pay(c, cs["id"]))
    assert all(r is not None and r.status_code < 500 for r in out), [r and r.text for r in out]
    assert succeeded_charges(pid) <= 1
    assert ledger_check(engine) == []
