import random
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import exc, text

from app import clock, ledger, ledger_check, seed

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def merchant(db):
    return seed.seed(db, "a@example.com")[0]


@pytest.fixture
def accts(db, merchant):
    return {
        "clearing": ledger.account_id(db, None, "card_network_clearing", "usd"),
        "fees": ledger.account_id(db, None, "platform_fees", "usd"),
        "pending": ledger.account_id(db, merchant.id, "merchant_pending", "usd"),
        "available": ledger.account_id(db, merchant.id, "merchant_available", "usd"),
    }


def entries(db, txn_id):
    rows = db.execute(
        text("SELECT account_id, amount, currency FROM journal_entries WHERE transaction_id = :t"),
        {"t": txn_id},
    ).all()
    return rows


def entries_by_kind(db, txn_id):
    rows = db.execute(
        text(
            "SELECT a.kind, e.amount FROM journal_entries e "
            "JOIN ledger_accounts a ON a.id = e.account_id WHERE e.transaction_id = :t"
        ),
        {"t": txn_id},
    ).all()
    assert len(rows) == len({k for k, _ in rows})
    return dict(rows)


def journal_ids(db):
    return set(db.scalars(text("SELECT id FROM journal_transactions")))


def new_jt(db, txn_id):
    db.execute(
        text(
            "INSERT INTO journal_transactions (id, type, source_id, created) "
            "VALUES (:id, 'test', 'src_test', now())"
        ),
        {"id": txn_id},
    )


def insert_entries(db, txn_id, rows):
    """One multi-row INSERT statement: rows are (account_id, amount, currency)."""
    params = {"t": txn_id}
    values = []
    for i, (acct, amount, cur) in enumerate(rows):
        values.append(f"(:t, :a{i}, :m{i}, :c{i})")
        params |= {f"a{i}": acct, f"m{i}": amount, f"c{i}": cur}
    db.execute(
        text(
            "INSERT INTO journal_entries (transaction_id, account_id, amount, currency) VALUES "
            + ", ".join(values)
        ),
        params,
    )


def assert_rejected(db, fn):
    with pytest.raises(exc.DBAPIError):
        with db.begin_nested():
            fn()


def bal(db, merchant_id):
    b = ledger.balance(db, merchant_id)
    return b["pending"][0]["amount"], b["available"][0]["amount"]


# --- fees ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(1, 1), (31, 31), (50, 31), (999, 59), (2000, 88), (100_000_000, 2_900_030)],
)
def test_fee(amount, expected):
    assert ledger.fee(amount) == expected


# --- post() --------------------------------------------------------------------------------


def test_post_balanced_three_legs(db, accts):
    tid = ledger.post(
        db,
        "test",
        "src_1",
        "usd",
        [(accts["clearing"], 100), (accts["pending"], -70), (accts["fees"], -30)],
    )
    assert isinstance(tid, str) and tid
    rows = entries(db, tid)
    assert len(rows) == 3
    assert sum(r.amount for r in rows) == 0
    assert {r.currency for r in rows} == {"usd"}


def test_post_drops_zero_legs(db, accts):
    tid = ledger.post(
        db,
        "test",
        "src_1",
        "usd",
        [(accts["clearing"], 100), (accts["pending"], -100), (accts["fees"], 0)],
    )
    rows = entries(db, tid)
    assert sorted((r.account_id, r.amount) for r in rows) == sorted(
        [(accts["clearing"], 100), (accts["pending"], -100)]
    )


# --- trigger (raw SQL) ---------------------------------------------------------------------


def test_trigger_accepts_balanced_two_leg_insert(db, accts):
    new_jt(db, "jtx_ok")
    with db.begin_nested():
        insert_entries(
            db, "jtx_ok", [(accts["clearing"], 100, "usd"), (accts["fees"], -100, "usd")]
        )
    assert len(entries(db, "jtx_ok")) == 2


def test_trigger_rejects_unbalanced(db, accts):
    new_jt(db, "jtx_t")
    assert_rejected(
        db,
        lambda: insert_entries(
            db, "jtx_t", [(accts["clearing"], 100, "usd"), (accts["fees"], -90, "usd")]
        ),
    )


def test_trigger_rejects_single_leg(db, accts):
    new_jt(db, "jtx_t")
    assert_rejected(db, lambda: insert_entries(db, "jtx_t", [(accts["clearing"], 100, "usd")]))


def test_trigger_rejects_mixed_currencies(db, accts):
    new_jt(db, "jtx_t")
    assert_rejected(
        db,
        lambda: insert_entries(
            db, "jtx_t", [(accts["clearing"], 100, "usd"), (accts["fees"], -100, "eur")]
        ),
    )


def test_trigger_rejects_zero_amount(db, accts):
    new_jt(db, "jtx_t")
    assert_rejected(
        db,
        lambda: insert_entries(
            db, "jtx_t", [(accts["clearing"], 0, "usd"), (accts["fees"], 0, "usd")]
        ),
    )


def test_trigger_rejects_appending_to_posted_transaction(db, accts):
    new_jt(db, "jtx_t")
    with db.begin_nested():
        insert_entries(db, "jtx_t", [(accts["clearing"], 100, "usd"), (accts["fees"], -100, "usd")])
    assert_rejected(
        db,
        lambda: insert_entries(
            db, "jtx_t", [(accts["clearing"], 5, "usd"), (accts["fees"], -5, "usd")]
        ),
    )
    assert len(entries(db, "jtx_t")) == 2


@pytest.fixture
def posted(db, accts):
    return ledger.post(
        db, "test", "src_1", "usd", [(accts["clearing"], 100), (accts["fees"], -100)]
    )


def test_update_journal_entries_is_rejected(db, posted):
    assert_rejected(
        db,
        lambda: db.execute(
            text("UPDATE journal_entries SET amount = amount * 2 WHERE transaction_id = :t"),
            {"t": posted},
        ),
    )


def test_delete_journal_entries_is_rejected(db, posted):
    assert_rejected(
        db,
        lambda: db.execute(
            text("DELETE FROM journal_entries WHERE transaction_id = :t"), {"t": posted}
        ),
    )


def test_update_journal_transactions_is_rejected(db, posted):
    assert_rejected(
        db,
        lambda: db.execute(
            text("UPDATE journal_transactions SET type = 'other' WHERE id = :t"), {"t": posted}
        ),
    )


def test_delete_journal_transactions_is_rejected(db, posted):
    assert_rejected(
        db,
        lambda: db.execute(text("DELETE FROM journal_transactions WHERE id = :t"), {"t": posted}),
    )


# --- accounts ------------------------------------------------------------------------------


def test_seeded_merchant_has_pending_and_available_usd_accounts(db, merchant):
    rows = db.execute(
        text("SELECT kind, currency FROM ledger_accounts WHERE merchant_id = :m"),
        {"m": merchant.id},
    ).all()
    assert sorted(rows) == [("merchant_available", "usd"), ("merchant_pending", "usd")]


def test_platform_accounts_exist(db):
    rows = db.execute(
        text("SELECT kind FROM ledger_accounts WHERE merchant_id IS NULL AND currency = 'usd'")
    ).all()
    assert sorted(r.kind for r in rows) == ["card_network_clearing", "platform_fees"]


def test_second_platform_account_of_same_kind_and_currency_fails(db):
    sql = text(
        "INSERT INTO ledger_accounts (id, merchant_id, kind, currency) "
        "VALUES (:id, NULL, 'platform_fees', :c)"
    )
    with db.begin_nested():  # control: a different currency is fine
        db.execute(sql, {"id": "la_fees_eur", "c": "eur"})
    assert_rejected(db, lambda: db.execute(sql, {"id": "la_fees_dup", "c": "usd"}))


def test_duplicate_merchant_account_fails(db, merchant):
    sql = text(
        "INSERT INTO ledger_accounts (id, merchant_id, kind, currency) "
        "VALUES (:id, :m, 'merchant_pending', :c)"
    )
    with db.begin_nested():  # control: a different currency is fine
        db.execute(sql, {"id": "la_pend_eur", "m": merchant.id, "c": "eur"})
    assert_rejected(
        db, lambda: db.execute(sql, {"id": "la_pend_dup", "m": merchant.id, "c": "usd"})
    )


# --- postings ------------------------------------------------------------------------------


@pytest.fixture
def fixed_now(monkeypatch):
    monkeypatch.setattr(clock, "now", lambda: T0)
    monkeypatch.delenv("AVAILABILITY_DELAY_SECONDS", raising=False)
    return T0


def test_capture_balance_transaction_fields(db, merchant, fixed_now):
    bt = ledger.post_capture(db, merchant.id, "ch_1", 1000)
    assert (bt.type, bt.status, bt.amount, bt.fee, bt.net) == ("charge", "pending", 1000, 59, 941)
    assert bt.currency == "usd"
    assert bt.merchant_id == merchant.id
    assert bt.source_id == "ch_1"
    assert bt.id.startswith("txn_")


def test_capture_available_on_defaults_to_two_days(db, merchant, fixed_now):
    bt = ledger.post_capture(db, merchant.id, "ch_1", 1000)
    assert bt.available_on == T0 + timedelta(days=2)


def test_capture_honours_availability_delay_env(db, merchant, fixed_now, monkeypatch):
    monkeypatch.setenv("AVAILABILITY_DELAY_SECONDS", "300")
    bt = ledger.post_capture(db, merchant.id, "ch_1", 1000)
    assert bt.available_on == T0 + timedelta(seconds=300)


def test_capture_entries(db, merchant):
    bt = ledger.post_capture(db, merchant.id, "ch_1", 1000)
    assert entries_by_kind(db, bt.journal_transaction_id) == {
        "card_network_clearing": 1000,
        "merchant_pending": -941,
        "platform_fees": -59,
    }


def test_capture_of_one_has_zero_net_and_drops_pending_leg(db, merchant):
    bt = ledger.post_capture(db, merchant.id, "ch_1", 1)
    assert (bt.amount, bt.fee, bt.net) == (1, 1, 0)
    assert entries_by_kind(db, bt.journal_transaction_id) == {
        "card_network_clearing": 1,
        "platform_fees": -1,
    }


def test_release_moves_net_from_pending_to_available(db, merchant):
    bt = ledger.post_capture(db, merchant.id, "ch_1", 1000)
    before = journal_ids(db)
    ledger.release(db, bt)
    assert bt.status == "available"
    new = journal_ids(db) - before
    assert len(new) == 1
    assert entries_by_kind(db, new.pop()) == {"merchant_pending": 941, "merchant_available": -941}


def test_balance_before_and_after_release(db, merchant):
    assert bal(db, merchant.id) == (0, 0)
    bt = ledger.post_capture(db, merchant.id, "ch_1", 1000)
    assert bal(db, merchant.id) == (941, 0)
    ledger.release(db, bt)
    assert bal(db, merchant.id) == (0, 941)


def test_balance_shape_for_fresh_merchant(db, merchant):
    assert ledger.balance(db, merchant.id) == {
        "available": [{"amount": 0, "currency": "usd"}],
        "pending": [{"amount": 0, "currency": "usd"}],
    }


def test_release_of_zero_net_posts_nothing(db, merchant):
    bt = ledger.post_capture(db, merchant.id, "ch_1", 1)
    before = db.scalar(text("SELECT count(*) FROM journal_transactions"))
    ledger.release(db, bt)
    assert bt.status == "available"
    assert db.scalar(text("SELECT count(*) FROM journal_transactions")) == before


def test_refund_after_capture_and_release(db, merchant):
    ledger.release(db, ledger.post_capture(db, merchant.id, "ch_1", 1000))
    bt = ledger.post_refund(db, merchant.id, "re_1", 400)
    assert (bt.type, bt.status, bt.amount, bt.fee, bt.net) == ("refund", "available", -400, 0, -400)
    assert entries_by_kind(db, bt.journal_transaction_id) == {
        "merchant_available": 400,
        "card_network_clearing": -400,
    }
    assert bal(db, merchant.id) == (0, 541)


def test_refund_larger_than_available_goes_negative(db, merchant):
    ledger.release(db, ledger.post_capture(db, merchant.id, "ch_1", 1000))
    ledger.post_refund(db, merchant.id, "re_1", 2000)
    assert bal(db, merchant.id) == (0, -1059)


# --- ledger_check --------------------------------------------------------------------------


def test_ledger_check_clean_after_random_activity(db):
    rng = random.Random(0)
    merchants = [seed.seed(db, f"m{i}@example.com")[0].id for i in range(2)]
    for i in range(20):
        m = rng.choice(merchants)
        amount = rng.randint(1, 100_000)
        bt = ledger.post_capture(db, m, f"ch_{i}", amount)
        if rng.random() < 0.6:
            ledger.release(db, bt)
        if rng.random() < 0.4:
            ledger.post_refund(db, m, f"re_{i}", rng.randint(1, amount))
    db.flush()
    assert ledger_check.ledger_check(db, own_txn=False) == []


def test_ledger_check_flags_stray_balance_transaction(db, merchant):
    ledger.post_capture(db, merchant.id, "ch_1", 1000)
    db.flush()
    assert ledger_check.ledger_check(db, own_txn=False) == []
    db.execute(
        text(
            "INSERT INTO balance_transactions (id, merchant_id, type, amount, fee, net, currency, "
            "status, available_on, source_id, journal_transaction_id, created) VALUES "
            "('txn_stray', :m, 'charge', 500, 0, 500, 'usd', 'pending', now(), 'ch_stray', "
            "NULL, now())"
        ),
        {"m": merchant.id},
    )
    problems = ledger_check.ledger_check(db, own_txn=False)
    assert problems and all(isinstance(p, str) for p in problems)


def test_ledger_check_cli_returns_zero():
    assert ledger_check.main() == 0
