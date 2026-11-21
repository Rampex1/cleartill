"""M23: payouts (ledger postings, API, settlement by housekeeping, reconciliation)."""

import re
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from sqlalchemy import text

from app import clock, housekeeping, ledger, seed
from app.ledger_check import ledger_check
from app.models import BalanceTransaction
from tests.test_housekeeping import T, _Begin, journal_count

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
DAY = 86400
FUNDED = 10000 - 320  # ledger.fee(10000) == 320
PAYOUT_KEYS = {
    "id",
    "object",
    "amount",
    "currency",
    "status",
    "arrival_date",
    "balance_transaction",
    "created",
    "livemode",
}


@pytest.fixture(autouse=True)
def _reconciles(db):
    yield
    db.flush()
    assert ledger_check(db, own_txn=False) == []


@pytest.fixture
def at(monkeypatch):
    monkeypatch.delenv("AVAILABILITY_DELAY_SECONDS", raising=False)

    def set_time(delta=timedelta(0)):
        when = T + delta
        monkeypatch.setattr(clock, "now", lambda: when)

    set_time()
    return set_time


@pytest.fixture
def keys(db):
    return seed.seed(db, "payouts-a@example.com")


@pytest.fixture
def mid(keys):
    return keys[0].id


@pytest.fixture
def skh(keys):
    return {"Authorization": f"Bearer {keys[1]}", **FORM}


@pytest.fixture
def funded(db, mid, at):
    """FUNDED cents available at T: captured 3 days earlier and released."""
    at(timedelta(days=-3))
    bt = ledger.post_capture(db, mid, "ch_funding", 10000)
    at()
    assert ledger.release(db, bt)
    assert ledger.fee(10000) == 10000 - FUNDED
    return FUNDED


@pytest.fixture
def other(db, at):
    m, sk, _ = seed.seed(db, "payouts-b@example.com")
    bt = ledger.post_capture(db, m.id, "ch_other", 10000)
    ledger.release(db, bt)
    return {"Authorization": f"Bearer {sk}", **FORM}


def payout(client, h, extra=None, **data):
    return client.post("/v1/payouts", content=urlencode(data), headers={**h, **(extra or {})})


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def get(client, h, path):
    return ok(client.get(path, headers=h))


def available(client, h):
    return get(client, h, "/v1/balance")["available"][0]["amount"]


def payout_ids(client, h):
    return [p["id"] for p in get(client, h, "/v1/payouts")["data"]]


def event_types(client, h):
    return [e["type"] for e in get(client, h, "/v1/events?limit=100")["data"]]


def assert_400(r, code=None, param=None):
    assert r.status_code == 400, r.text
    e = r.json()["error"]
    if code is not None:
        assert e["code"] == code
    if param is not None:
        assert e["param"] == param


def entries_by_kind(db, jtx_ids):
    rows = db.execute(
        text(
            "SELECT a.kind, sum(e.amount) FROM journal_entries e "
            "JOIN ledger_accounts a ON a.id = e.account_id "
            "WHERE e.transaction_id = ANY(:t) GROUP BY a.kind"
        ),
        {"t": list(jtx_ids)},
    ).all()
    return {k: int(v) for k, v in rows}


def journal_ids(db):
    return set(db.scalars(text("SELECT id FROM journal_transactions")))


def in_transit(db):
    acct = ledger.account_id(db, None, "payouts_in_transit", "usd")
    return db.scalar(
        text("SELECT coalesce(sum(amount), 0) FROM journal_entries WHERE account_id = :a"),
        {"a": acct},
    )


def make_payout(db, mid, amount):
    from app import payouts

    return payouts.create_payout(db, mid, amount)


# --- ledger --------------------------------------------------------------------------------


def test_payout_balance_transaction_fields(db, mid, funded):
    from app import payouts

    p = make_payout(db, mid, 2500)
    body = payouts.payout_json(p)
    bt = db.get(BalanceTransaction, body["balance_transaction"])
    assert (bt.type, bt.amount, bt.fee, bt.net) == ("payout", -2500, 0, -2500)
    assert (bt.status, bt.source_id, bt.currency) == ("available", p.id, "usd")
    assert bt.merchant_id == mid


def test_post_payout_moves_available_to_in_transit(db, mid, funded):
    before = journal_ids(db)
    make_payout(db, mid, 2500)
    new = journal_ids(db) - before
    assert len(new) == 1
    assert entries_by_kind(db, new) == {"merchant_available": 2500, "payouts_in_transit": -2500}
    assert ledger.balance(db, mid)["available"][0]["amount"] == FUNDED - 2500


def test_settle_payout_debits_in_transit_and_credits_clearing(db, mid, funded):
    p = make_payout(db, mid, 2500)
    before = journal_ids(db)
    assert ledger.settle_payout(db, p) is True
    new = journal_ids(db) - before
    assert entries_by_kind(db, new) == {"payouts_in_transit": 2500, "card_network_clearing": -2500}
    db.refresh(p)
    assert p.status == "paid"
    assert in_transit(db) == 0


def test_settle_payout_twice_posts_nothing(db, mid, funded):
    p = make_payout(db, mid, 2500)
    assert ledger.settle_payout(db, p) is True
    before = journal_count(db)
    assert ledger.settle_payout(db, p) is False
    assert journal_count(db) == before
    db.refresh(p)
    assert p.status == "paid"


def test_lock_available_can_be_called_inside_the_transaction(db, mid, funded):
    ledger.lock_available(db, mid, "usd")
    make_payout(db, mid, 100)  # the lock is re-entrant for the same transaction


# --- POST /v1/payouts ----------------------------------------------------------------------


def test_create_returns_pending_payout(client, skh, funded):
    body = ok(payout(client, skh, amount=2500, currency="usd"))
    assert set(body) == PAYOUT_KEYS
    assert re.fullmatch(r"po_\w+", body["id"])
    assert re.fullmatch(r"txn_\w+", body["balance_transaction"])
    assert (body["object"], body["amount"], body["currency"]) == ("payout", 2500, "usd")
    assert (body["status"], body["livemode"]) == ("pending", False)
    assert body["created"] == int(T.timestamp())
    assert body["arrival_date"] == body["created"] + 2 * DAY


def test_arrival_date_follows_availability_delay(client, skh, funded, monkeypatch):
    monkeypatch.setenv("AVAILABILITY_DELAY_SECONDS", "3600")
    body = ok(payout(client, skh, amount=100, currency="usd"))
    assert body["arrival_date"] == body["created"] + 3600


def test_create_emits_payout_created(client, skh, funded):
    body = ok(payout(client, skh, amount=2500, currency="usd"))
    events = get(client, skh, "/v1/events?limit=100")["data"]
    created = [e for e in events if e["type"] == "payout.created"]
    assert len(created) == 1
    assert created[0]["data"]["object"]["id"] == body["id"]


def test_create_reduces_available_balance(client, skh, funded):
    ok(payout(client, skh, amount=2500, currency="usd"))
    assert available(client, skh) == FUNDED - 2500


def test_create_lists_a_payout_balance_transaction(client, skh, funded):
    body = ok(payout(client, skh, amount=2500, currency="usd"))
    bts = [b for b in get(client, skh, "/v1/balance_transactions")["data"] if b["type"] == "payout"]
    assert len(bts) == 1
    bt = bts[0]
    assert bt["id"] == body["balance_transaction"]
    assert (bt["type"], bt["amount"], bt["fee"], bt["net"]) == ("payout", -2500, 0, -2500)
    assert (bt["status"], bt["source"]) == ("available", body["id"])


def test_balance_transactions_filter_by_payout_type(client, skh, funded):
    body = ok(payout(client, skh, amount=2500, currency="usd"))
    bts = get(client, skh, "/v1/balance_transactions?type=payout")["data"]
    assert [b["id"] for b in bts] == [body["balance_transaction"]]


def test_payout_of_entire_available_balance(client, skh, funded):
    ok(payout(client, skh, amount=FUNDED, currency="usd"))
    assert available(client, skh) == 0


def test_two_payouts_drain_balance_then_third_is_insufficient(client, skh, funded):
    ok(payout(client, skh, amount=FUNDED - 1, currency="usd"))
    ok(payout(client, skh, amount=1, currency="usd"))
    assert_400(payout(client, skh, amount=1, currency="usd"), "balance_insufficient", "amount")
    assert len(payout_ids(client, skh)) == 2


# --- errors --------------------------------------------------------------------------------


def test_amount_over_available_is_balance_insufficient(client, skh, funded):
    r = payout(client, skh, amount=FUNDED + 1, currency="usd")
    assert_400(r, "balance_insufficient", "amount")
    assert payout_ids(client, skh) == []
    assert available(client, skh) == FUNDED
    assert "payout.created" not in event_types(client, skh)


def test_fresh_merchant_cannot_pay_out(client, skh, at):
    assert_400(payout(client, skh, amount=1, currency="usd"), "balance_insufficient", "amount")


def test_pending_funds_cannot_be_paid_out(client, skh, db, mid, at):
    ledger.post_capture(db, mid, "ch_pending", 10000)
    assert_400(payout(client, skh, amount=100, currency="usd"), "balance_insufficient", "amount")


def test_error_message_does_not_echo_the_amount(client, skh, funded):
    r = payout(client, skh, amount=54321, currency="usd")
    assert_400(r, "balance_insufficient")
    assert "54321" not in r.text


@pytest.mark.parametrize(
    "data",
    [
        {"amount": "0", "currency": "usd"},
        {"currency": "usd"},
        {"amount": "500", "currency": "eur"},
        {"amount": "-500", "currency": "usd"},
        {"amount": "5.00", "currency": "usd"},
        {"amount": "abc", "currency": "usd"},
        {"amount": "", "currency": "usd"},
    ],
    ids=["zero", "missing_amount", "eur", "negative", "decimal", "text", "empty"],
)
def test_invalid_params_are_400_and_create_nothing(client, skh, funded, data):
    assert_400(payout(client, skh, **data))
    assert payout_ids(client, skh) == []
    assert available(client, skh) == FUNDED


def test_publishable_key_is_403(client, keys, funded):
    pkh = {"Authorization": f"Bearer {keys[2]}", **FORM}
    r = payout(client, pkh, amount=100, currency="usd")
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "permission_denied"


def test_unauthenticated_is_401(client, funded):
    assert payout(client, FORM, amount=100, currency="usd").status_code == 401


def test_idempotency_key_replays_the_same_payout(client, skh, funded):
    key = {"Idempotency-Key": "po-k1"}
    first = ok(payout(client, skh, key, amount=2500, currency="usd"))
    second = ok(payout(client, skh, key, amount=2500, currency="usd"))
    assert second == first
    assert payout_ids(client, skh) == [first["id"]]
    assert available(client, skh) == FUNDED - 2500
    assert event_types(client, skh).count("payout.created") == 1


# --- retrieve and list ---------------------------------------------------------------------


def test_retrieve_payout(client, skh, funded):
    body = ok(payout(client, skh, amount=2500, currency="usd"))
    assert get(client, skh, f"/v1/payouts/{body['id']}") == body


def test_retrieve_unknown_is_404(client, skh, at):
    r = client.get("/v1/payouts/po_doesnotexist", headers=skh)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "resource_missing"


def test_retrieve_other_merchants_payout_is_404(client, skh, other, funded):
    theirs = ok(payout(client, other, amount=100, currency="usd"))
    r = client.get(f"/v1/payouts/{theirs['id']}", headers=skh)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "resource_missing"


def test_list_shows_only_own_payouts_newest_first(client, skh, other, funded):
    a = ok(payout(client, skh, amount=100, currency="usd"))
    ok(payout(client, other, amount=100, currency="usd"))
    b = ok(payout(client, skh, amount=200, currency="usd"))
    body = get(client, skh, "/v1/payouts")
    assert (body["object"], body["has_more"]) == ("list", False)
    assert [p["id"] for p in body["data"]] == [b["id"], a["id"]]


def test_list_paginates(client, skh, funded):
    ids = [ok(payout(client, skh, amount=100, currency="usd"))["id"] for _ in range(3)]
    r1 = get(client, skh, "/v1/payouts?limit=2")
    assert [p["id"] for p in r1["data"]] == [ids[2], ids[1]] and r1["has_more"] is True
    r2 = get(client, skh, f"/v1/payouts?limit=2&starting_after={ids[1]}")
    assert [p["id"] for p in r2["data"]] == [ids[0]] and r2["has_more"] is False


def test_list_with_publishable_key_is_403(client, keys):
    r = client.get("/v1/payouts", headers={"Authorization": f"Bearer {keys[2]}"})
    assert r.status_code == 403


# --- settlement ----------------------------------------------------------------------------


def test_pay_due_payouts_pays_after_arrival(client, skh, db, funded, at):
    from app import payouts

    body = ok(payout(client, skh, amount=2500, currency="usd"))
    at(timedelta(seconds=2 * DAY + 1))
    assert payouts.pay_due_payouts(db) == 1
    assert get(client, skh, f"/v1/payouts/{body['id']}")["status"] == "paid"
    assert "payout.paid" in event_types(client, skh)
    assert in_transit(db) == 0
    assert available(client, skh) == FUNDED - 2500


def test_pay_due_payouts_before_arrival_does_nothing(client, skh, db, funded, at):
    from app import payouts

    body = ok(payout(client, skh, amount=2500, currency="usd"))
    before = journal_count(db)
    at(timedelta(seconds=2 * DAY - 1))
    assert payouts.pay_due_payouts(db) == 0
    assert journal_count(db) == before
    assert get(client, skh, f"/v1/payouts/{body['id']}")["status"] == "pending"
    assert in_transit(db) == -2500
    assert "payout.paid" not in event_types(client, skh)


def test_second_pay_due_payouts_run_returns_zero(client, skh, db, funded, at):
    from app import payouts

    ok(payout(client, skh, amount=2500, currency="usd"))
    at(timedelta(days=3))
    assert payouts.pay_due_payouts(db) == 1
    before = journal_count(db)
    assert payouts.pay_due_payouts(db) == 0
    assert journal_count(db) == before
    assert event_types(client, skh).count("payout.paid") == 1


def test_pay_due_payouts_pays_only_due_ones(client, skh, db, funded, at):
    from app import payouts

    first = ok(payout(client, skh, amount=100, currency="usd"))
    at(timedelta(days=1))
    second = ok(payout(client, skh, amount=200, currency="usd"))
    at(timedelta(days=2, seconds=1))
    assert payouts.pay_due_payouts(db) == 1
    assert get(client, skh, f"/v1/payouts/{first['id']}")["status"] == "paid"
    assert get(client, skh, f"/v1/payouts/{second['id']}")["status"] == "pending"
    assert in_transit(db) == -200


def test_run_housekeeping_reports_paid_payouts(client, skh, db, funded, at):
    ok(payout(client, skh, amount=2500, currency="usd"))
    at(timedelta(days=3))
    result = housekeeping.run_housekeeping(_Begin(db))
    assert result["paid_payouts"] == 1


# --- reconciliation ------------------------------------------------------------------------


def test_create_and_settle_reconcile(db, mid, funded, at):
    from app import payouts

    make_payout(db, mid, 2500)
    db.flush()
    assert ledger_check(db, own_txn=False) == []
    at(timedelta(days=3))
    assert payouts.pay_due_payouts(db) == 1
    db.flush()
    assert ledger_check(db, own_txn=False) == []


def test_paid_status_without_posting_is_reported(db, mid, funded):
    p = make_payout(db, mid, 2500)
    db.flush()
    db.execute(text("UPDATE payouts SET status = 'paid' WHERE id = :p"), {"p": p.id})
    assert ledger_check(db, own_txn=False) != []
    db.execute(text("UPDATE payouts SET status = 'pending' WHERE id = :p"), {"p": p.id})
    db.expire_all()
