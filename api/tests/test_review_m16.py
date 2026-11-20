# ruff: noqa: F811  (pytest fixtures imported from test_3ds)
"""Regression tests for the M16 review: merchant query preserved, canceled PI, event order,
readable state message, and concurrent challenges (committed data)."""

import threading

from fastapi.testclient import TestClient
from sqlalchemy import text

from app import seed
from app.db import SessionLocal
from app.ids import new_id
from app.ledger_check import ledger_check
from app.main import app
from tests.test_3ds import (  # noqa: F401
    FORM,
    assert_400,
    challenge,
    keys,
    ledger_clean,
    pkh,
    post,
    requires_action,
    skh,
)


def test_redirect_keeps_merchant_query_bytes(client, skh, pkh):
    url = "https://example.com/done?q=a%20b&flag&next=/a"
    pi, ra = requires_action(client, skh, pkh, return_url=url)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="approve")
    assert r.json()["redirect"] == f"{url}&payment_intent={pi['id']}"


def test_canceled_pi_cannot_be_challenged(client, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    assert post(client, skh, f"/v1/payment_intents/{pi['id']}/cancel").status_code == 200
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="approve")
    assert_400(r, "payment_intent_unexpected_state")
    assert "authenticate" in r.json()["error"]["message"]


def test_events_are_charge_first_and_carry_the_request_id(client, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="approve")
    assert r.status_code == 200
    got = client.get("/v1/events", headers=skh).json()["data"]  # newest first
    types = [e["type"] for e in got]
    assert types.index("payment_intent.succeeded") < types.index("charge.succeeded")
    for e in got[:2]:
        assert e["request"]["id"] == r.headers["Request-Id"]


# --- concurrency (committed data; ledger rows stay behind, as CLAUDE.md allows) -------------


def committed_requires_action():
    with SessionLocal.begin() as s:
        _, sk, pk = seed.seed(s, f"{new_id('tds_')}@example.com")
    skh_, pkh_ = (
        {"Authorization": f"Bearer {sk}", **FORM},
        {"Authorization": f"Bearer {pk}", **FORM},
    )
    with TestClient(app) as c:
        return c, skh_, requires_action(c, skh_, pkh_)[0]


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
    return out


def charge_count(pi_id):
    with SessionLocal() as s:
        return s.scalar(
            text("SELECT count(*) FROM charges WHERE payment_intent_id = :p"), {"p": pi_id}
        )


def test_two_simultaneous_approves_charge_once(engine):
    _, _, pi = committed_requires_action()

    def approve(c):
        return challenge(c, pi["id"], client_secret=pi["client_secret"], result="approve")

    codes = sorted(r.status_code for r in race(approve, approve))
    assert codes == [200, 400]
    assert charge_count(pi["id"]) == 1
    assert ledger_check(engine) == []


def test_approve_racing_cancel_has_one_winner(engine):
    _, skh_, pi = committed_requires_action()

    def approve(c):
        return challenge(c, pi["id"], client_secret=pi["client_secret"], result="approve")

    def cancel(c):
        return post(c, skh_, f"/v1/payment_intents/{pi['id']}/cancel")

    a, b = race(approve, cancel)
    assert sorted([a.status_code, b.status_code]) == [200, 400]
    with TestClient(app) as c:
        status = c.get(f"/v1/payment_intents/{pi['id']}", headers=skh_).json()["status"]
    assert (status, charge_count(pi["id"])) in {("succeeded", 1), ("canceled", 0)}
    assert ledger_check(engine) == []
