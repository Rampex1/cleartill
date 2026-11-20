from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

API_DIR = Path(__file__).resolve().parent.parent


def test_downgrade_then_upgrade_creates_tables(engine):
    cfg = Config(str(API_DIR / "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        for table in (
            "merchants",
            "api_keys",
            "customers",
            "idempotency_keys",
            "dashboard_sessions",
            "ledger_accounts",
            "journal_transactions",
            "journal_entries",
            "balance_transactions",
            "events",
            "payment_methods",
            "payment_intents",
        ):
            assert conn.execute(text(f"SELECT to_regclass('{table}')")).scalar() == table


def test_upgrade_backfills_ledger_accounts_for_existing_merchants(engine):
    cfg = Config(str(API_DIR / "alembic.ini"))
    command.downgrade(cfg, "0004")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO merchants (id, email, created) "
                "VALUES ('acct_backfill', 'bf@example.com', now())"
            )
        )
    try:
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT kind, currency FROM ledger_accounts WHERE merchant_id = 'acct_backfill'"
                )
            ).all()
        assert sorted(rows) == [("merchant_available", "usd"), ("merchant_pending", "usd")]
    finally:
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM merchants WHERE id = 'acct_backfill'"))
