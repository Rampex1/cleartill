"""Reconciliation: the ledger's invariants, checked from the outside.

`python -m app.ledger_check` exits 1 on any problem. M9b/M11 add invariant 4 (clearing vs
charges and refunds)."""

import sys

from sqlalchemy import text

PROBLEM_QUERIES = {
    "entries don't sum to 0": """
        SELECT currency, sum(amount) FROM journal_entries GROUP BY currency HAVING sum(amount) <> 0
    """,
    "journal transaction is single-legged, unbalanced or multi-currency": """
        SELECT t.id, count(e.id), coalesce(sum(e.amount), 0), count(DISTINCT e.currency)
        FROM journal_transactions t
        LEFT JOIN journal_entries e ON e.transaction_id = t.id
        GROUP BY t.id
        HAVING count(e.id) < 2 OR coalesce(sum(e.amount), 0) <> 0
            OR count(DISTINCT e.currency) > 1
    """,
    "entry currency differs from its account's": """
        SELECT e.id, e.currency, a.currency FROM journal_entries e
        JOIN ledger_accounts a ON a.id = e.account_id WHERE e.currency <> a.currency
    """,
    "balance transaction where amount - fee <> net": """
        SELECT id, amount, fee, net FROM balance_transactions WHERE amount - fee <> net
    """,
    "merchant account doesn't match its balance transactions": """
        WITH acct AS (
            SELECT a.merchant_id, a.currency, a.kind, -coalesce(sum(e.amount), 0) AS balance
            FROM ledger_accounts a LEFT JOIN journal_entries e ON e.account_id = a.id
            WHERE a.merchant_id IS NOT NULL
            GROUP BY a.merchant_id, a.currency, a.kind
        ), bt AS (
            SELECT merchant_id, currency,
                   CASE status WHEN 'pending' THEN 'merchant_pending'
                               ELSE 'merchant_available' END AS kind,
                   sum(net) AS net
            FROM balance_transactions GROUP BY 1, 2, 3
        )
        SELECT coalesce(acct.merchant_id, bt.merchant_id), coalesce(acct.kind, bt.kind),
               coalesce(acct.balance, 0), coalesce(bt.net, 0)
        FROM acct FULL JOIN bt
          ON bt.merchant_id = acct.merchant_id AND bt.currency = acct.currency
         AND bt.kind = acct.kind
        WHERE coalesce(acct.balance, 0) <> coalesce(bt.net, 0)
    """,
    "charge's amount_captured doesn't match its capture balance transactions": """
        SELECT c.id, c.amount_captured, coalesce(sum(bt.amount), 0)
        FROM charges c
        LEFT JOIN balance_transactions bt
          ON bt.source_id = c.id AND bt.type = 'charge'
         AND bt.merchant_id = c.merchant_id AND bt.currency = c.currency
        GROUP BY c.id, c.amount_captured
        HAVING c.amount_captured <> coalesce(sum(bt.amount), 0)
    """,
    "charge's amount_refunded doesn't match its refunds": """
        SELECT c.id, c.amount_refunded, coalesce(sum(r.amount), 0)
        FROM charges c
        LEFT JOIN refunds r
          ON r.charge_id = c.id AND r.status = 'succeeded'
         AND r.merchant_id = c.merchant_id AND r.currency = c.currency
        GROUP BY c.id, c.amount_refunded
        HAVING c.amount_refunded <> coalesce(sum(r.amount), 0)
    """,
    "refund doesn't match its balance transaction": """
        SELECT r.id, r.amount, bt.amount FROM refunds r
        LEFT JOIN balance_transactions bt
          ON bt.id = r.balance_transaction_id AND bt.source_id = r.id AND bt.type = 'refund'
         AND bt.merchant_id = r.merchant_id AND bt.currency = r.currency
        WHERE bt.id IS NULL OR bt.amount <> -r.amount
    """,
    "payouts in transit don't match pending payouts": """
        WITH transit AS (
            SELECT a.currency, -coalesce(sum(e.amount), 0) AS booked
            FROM ledger_accounts a LEFT JOIN journal_entries e ON e.account_id = a.id
            WHERE a.kind = 'payouts_in_transit' GROUP BY a.currency
        ), pending AS (
            SELECT currency, sum(amount) AS owed FROM payouts WHERE status = 'pending' GROUP BY 1
        )
        SELECT coalesce(transit.currency, pending.currency), coalesce(booked, 0),
               coalesce(owed, 0)
        FROM transit FULL JOIN pending ON pending.currency = transit.currency
        WHERE coalesce(booked, 0) <> coalesce(owed, 0)
    """,
    "payout doesn't match its balance transaction": """
        SELECT p.id, p.amount, bt.amount FROM payouts p
        LEFT JOIN balance_transactions bt
          ON bt.id = p.balance_transaction_id AND bt.source_id = p.id AND bt.type = 'payout'
         AND bt.merchant_id = p.merchant_id AND bt.currency = p.currency
        WHERE bt.id IS NULL OR bt.amount <> -p.amount
    """,
    "card clearing doesn't match captures - refunds - paid payouts": """
        WITH clearing AS (
            SELECT a.currency, coalesce(sum(e.amount), 0) AS booked
            FROM ledger_accounts a LEFT JOIN journal_entries e ON e.account_id = a.id
            WHERE a.kind = 'card_network_clearing' GROUP BY a.currency
        ), expected AS (
            SELECT currency, sum(amount) AS due FROM (
                SELECT currency, amount FROM balance_transactions
                WHERE type IN ('charge', 'refund')
                UNION ALL
                SELECT currency, -amount FROM payouts WHERE status = 'paid'
            ) flows GROUP BY 1
        )
        SELECT coalesce(clearing.currency, expected.currency), coalesce(booked, 0),
               coalesce(due, 0)
        FROM clearing FULL JOIN expected ON expected.currency = clearing.currency
        WHERE coalesce(booked, 0) <> coalesce(due, 0)
    """,
    "platform fees don't match balance transaction fees": """
        WITH fees AS (
            SELECT a.currency, -coalesce(sum(e.amount), 0) AS booked
            FROM ledger_accounts a LEFT JOIN journal_entries e ON e.account_id = a.id
            WHERE a.kind = 'platform_fees' GROUP BY a.currency
        ), bt AS (SELECT currency, sum(fee) AS charged FROM balance_transactions GROUP BY 1)
        SELECT coalesce(fees.currency, bt.currency), coalesce(booked, 0), coalesce(charged, 0)
        FROM fees FULL JOIN bt ON bt.currency = fees.currency
        WHERE coalesce(booked, 0) <> coalesce(charged, 0)
    """,
}


def _run(conn) -> list[str]:
    problems = []
    for label, sql in PROBLEM_QUERIES.items():
        for row in conn.execute(text(sql)).all():
            problems.append(f"{label}: {tuple(row)}")
    return problems


def ledger_check(bind, own_txn: bool = True) -> list[str]:
    """Return a list of problems (empty = consistent).

    own_txn=True: `bind` must be an Engine (not a Session); the checks run in one
    REPEATABLE READ READ ONLY transaction, so a concurrent posting can't cause a false alarm.
    own_txn=False: `bind` is the caller's Session/Connection (tests call it before rolling
    back)."""
    if not own_txn:
        return _run(bind)
    with bind.connect() as conn:
        conn = conn.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
        with conn.begin():
            return _run(conn)


def main() -> int:
    from app.db import engine

    problems = ledger_check(engine)
    for problem in problems:
        print(problem, file=sys.stderr)
    print("ledger OK" if not problems else f"{len(problems)} ledger problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
