# ruff: noqa: F811  (pytest fixtures imported from test_checkout)
"""Regression tests for the M17 review: templated success_url after 3DS, 3DS on an expired
session, per-row expiry, failed-attempt cap, quantity errors, refunds on checkout PIs,
card data in no table or log, migration, and expiry racing pay/3DS."""

import logging
import threading
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app import checkout, clock
from app.db import SessionLocal
from app.ledger_check import ledger_check
from app.main import app
from tests.test_checkout import (  # noqa: F401
    DECLINE,
    THREE_DS,
    assert_400,
    challenge,
    create_raw,
    create_session,
    get_session,
    keys,
    later,
    ledger_clean,
    pay,
    post,
    public,
    retrieve_pi,
    session_body,
    skh,
    three_ds_paid,
)
from tests.test_checkout_concurrency import committed_session
from tests.test_checkout_concurrency import pay as raw_pay


def test_public_success_url_is_templated_after_3ds(client, skh):
    cs = create_session(client, skh, success_url="https://example.com/ok?cs={CHECKOUT_SESSION_ID}")
    r = pay(client, cs["id"], THREE_DS)
    secret = r.json()["redirect"].split("client_secret=")[1]
    assert (
        challenge(client, cs["payment_intent"], client_secret=secret, result="approve").status_code
        == 200
    )
    view = public(client, cs["id"]).json()
    assert view["status"] == "complete"
    assert view["success_url"] == f"https://example.com/ok?cs={cs['id']}"


def test_3ds_approve_after_expiry_is_rejected(client, skh, monkeypatch):
    cs, secret = three_ds_paid(client, skh)
    later(monkeypatch)
    r = challenge(client, cs["payment_intent"], client_secret=secret, result="approve")
    assert_400(r, "checkout_session_unexpected_state")
    assert retrieve_pi(client, skh, cs["payment_intent"])["status"] == "requires_action"
    assert get_session(client, skh, cs["id"])["status"] == "open"


def test_one_bad_session_does_not_block_expiry(client, db, skh, monkeypatch):
    first = create_session(client, skh)
    second = create_session(client, skh)
    later(monkeypatch)
    real = checkout._expire

    def flaky(db_, cs):
        if cs.id == first["id"]:
            raise RuntimeError("bad row")
        return real(db_, cs)

    monkeypatch.setattr(checkout, "_expire", flaky)
    assert checkout.expire_sessions(db) == 1
    assert get_session(client, skh, first["id"])["status"] == "open"
    assert get_session(client, skh, second["id"])["status"] == "expired"


def test_failed_attempts_are_capped(client, skh, monkeypatch):
    monkeypatch.setattr(checkout, "MAX_FAILED_ATTEMPTS", 2)
    cs = create_session(client, skh)
    assert [pay(client, cs["id"], DECLINE).status_code for _ in range(2)] == [402, 402]
    assert_400(pay(client, cs["id"]), "checkout_session_unexpected_state")


def test_zero_quantity_is_a_quantity_error(client, skh):
    r = create_raw(client, skh, session_body(**{"line_items[0][quantity]": "0"}))
    assert_400(r, "parameter_invalid_integer", param="line_items")


def test_checkout_pi_can_be_refunded(client, skh):
    cs = create_session(client, skh)
    assert pay(client, cs["id"]).status_code == 200
    r = post(client, skh, "/v1/refunds", {"payment_intent": cs["payment_intent"]})
    assert r.status_code == 200, r.text


def test_card_data_is_in_no_table_or_log(client, db, skh, caplog):
    numbers = [DECLINE, "4242424242424241", "42424242424242x2"]
    with caplog.at_level(logging.DEBUG):
        cs = create_session(client, skh)
        for number in numbers:
            r = pay(client, cs["id"], number, **{"card[cvc]": "987"})
            assert number not in r.text and "987" not in r.text
    db.flush()
    dump = db.scalar(
        text(
            "SELECT string_agg(t::text, '') FROM ("
            " SELECT row_to_json(e)::text t FROM events e UNION ALL"
            " SELECT row_to_json(c)::text FROM charges c UNION ALL"
            " SELECT row_to_json(p)::text FROM payment_intents p UNION ALL"
            " SELECT row_to_json(m)::text FROM payment_methods m UNION ALL"
            " SELECT row_to_json(w)::text FROM webhook_deliveries w) x"
        )
    )
    for number in numbers:
        assert number not in dump
        assert number not in caplog.text


def test_migration_creates_checkout_sessions(db):
    assert "checkout_sessions" in inspect(db.connection()).get_table_names()


# --- expiry racing pay and 3DS (committed data) ---------------------------------------------


def race(*fns):
    barrier, out = threading.Barrier(len(fns)), [None] * len(fns)

    def run(i, fn):
        barrier.wait()
        out[i] = fn()

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert all(not t.is_alive() for t in threads), "deadlock or hang"
    return out


def expire_committed():
    with SessionLocal.begin() as s:
        return checkout.expire_sessions(s)


def test_expiry_racing_pay_and_3ds_is_consistent(engine, monkeypatch):
    _, cs = committed_session()
    _, cs_3ds = committed_session()
    with TestClient(app) as c:
        secret = raw_pay(c, cs_3ds["id"], THREE_DS).json()["redirect"].split("client_secret=")[1]
    when = clock.now() + timedelta(hours=24, seconds=1)
    monkeypatch.setattr(clock, "now", lambda: when)

    def pay_it():
        with TestClient(app) as c:
            return raw_pay(c, cs["id"])

    def approve_it():
        with TestClient(app) as c:
            return challenge(c, cs_3ds["payment_intent"], client_secret=secret, result="approve")

    r_pay, r_3ds, _ = race(pay_it, approve_it, expire_committed)
    assert r_pay.status_code == 400 and r_3ds.status_code == 400
    expire_committed()  # rows a request held were skipped (SKIP LOCKED); the next run gets them
    with SessionLocal() as s:
        for session in (cs, cs_3ds):
            row = s.execute(
                text(
                    "SELECT cs.status, pi.status AS pi_status, "
                    "(SELECT count(*) FROM charges WHERE payment_intent_id = pi.id "
                    " AND status = 'succeeded') AS ok "
                    "FROM checkout_sessions cs "
                    "JOIN payment_intents pi ON pi.id = cs.payment_intent_id "
                    "WHERE cs.id = :id"
                ),
                {"id": session["id"]},
            ).one()
            assert (row.status, row.pi_status, row.ok) == ("expired", "canceled", 0)
    assert ledger_check(engine) == []
