"""ledger: accounts, append-only balanced journal, balance transactions

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

BALANCE_CHECK = """
CREATE FUNCTION journal_entries_check() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $$
DECLARE
    bad text;
BEGIN
    -- Serialise concurrent statements writing to the same transaction (each trigger only sees
    -- its own statement's rows). A row lock doesn't fire the UPDATE trigger.
    PERFORM 1 FROM journal_transactions
    WHERE id IN (SELECT DISTINCT transaction_id FROM new_rows) FOR UPDATE;
    -- For every transaction touched by this statement: all of its entries arrived in this
    -- statement (no appending to a posted transaction), at least 2 legs, sum 0, one currency.
    SELECT n.transaction_id INTO bad
    FROM (SELECT transaction_id, count(*) AS new_count FROM new_rows GROUP BY transaction_id) n
    JOIN LATERAL (
        SELECT count(*) AS total, sum(amount) AS total_amount,
               count(DISTINCT currency) AS currencies
        FROM journal_entries e WHERE e.transaction_id = n.transaction_id
    ) t ON true
    WHERE t.total <> n.new_count OR t.total < 2 OR t.total_amount <> 0 OR t.currencies <> 1
    LIMIT 1;
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'invalid journal transaction %', bad
            USING ERRCODE = 'check_violation',
                  DETAIL = 'unbalanced, single-legged, multi-currency or appended to';
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER journal_entries_balanced
    AFTER INSERT ON journal_entries
    REFERENCING NEW TABLE AS new_rows
    FOR EACH STATEMENT EXECUTE FUNCTION journal_entries_check();

CREATE FUNCTION journal_append_only() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $$
BEGIN
    RAISE EXCEPTION 'the journal is append-only: post a reversing transaction instead'
        USING ERRCODE = 'restrict_violation';
END $$;

CREATE TRIGGER journal_entries_append_only
    BEFORE UPDATE OR DELETE ON journal_entries
    FOR EACH ROW EXECUTE FUNCTION journal_append_only();

CREATE TRIGGER journal_transactions_append_only
    BEFORE UPDATE OR DELETE ON journal_transactions
    FOR EACH ROW EXECUTE FUNCTION journal_append_only();

CREATE TRIGGER journal_entries_no_truncate
    BEFORE TRUNCATE ON journal_entries
    FOR EACH STATEMENT EXECUTE FUNCTION journal_append_only();

CREATE TRIGGER journal_transactions_no_truncate
    BEFORE TRUNCATE ON journal_transactions
    FOR EACH STATEMENT EXECUTE FUNCTION journal_append_only();
"""


def upgrade() -> None:
    op.create_table(
        "ledger_accounts",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("merchant_id", sa.Text, sa.ForeignKey("merchants.id", ondelete="CASCADE")),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("currency", sa.Text, nullable=False),
        sa.UniqueConstraint("merchant_id", "kind", "currency", name="uq_ledger_accounts_merchant"),
        sa.CheckConstraint(
            "kind IN ('merchant_pending', 'merchant_available', "
            "'card_network_clearing', 'platform_fees')",
            name="ledger_accounts_kind",
        ),
        sa.UniqueConstraint("id", "currency", name="uq_ledger_accounts_id_currency"),
    )
    op.create_index(
        "ux_ledger_accounts_platform",
        "ledger_accounts",
        ["kind", "currency"],
        unique=True,
        postgresql_where=sa.text("merchant_id IS NULL"),
    )
    op.create_table(
        "journal_transactions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("source_id", sa.Text, nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "journal_entries",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "transaction_id",
            sa.Text,
            sa.ForeignKey("journal_transactions.id"),
            nullable=False,
        ),
        sa.Column("account_id", sa.Text, nullable=False),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.Text, nullable=False),
        sa.CheckConstraint("amount <> 0", name="journal_entries_nonzero"),
        sa.ForeignKeyConstraint(
            ["account_id", "currency"],
            ["ledger_accounts.id", "ledger_accounts.currency"],
            name="fk_journal_entries_account_currency",
        ),
    )
    op.create_index("ix_journal_entries_transaction_id", "journal_entries", ["transaction_id"])
    op.create_index("ix_journal_entries_account_id", "journal_entries", ["account_id"])
    op.create_table(
        "balance_transactions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False, unique=True),
        # No CASCADE: a merchant with money history can't be deleted.
        sa.Column("merchant_id", sa.Text, sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("fee", sa.BigInteger, nullable=False),
        sa.Column("net", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("available_on", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_id", sa.Text, nullable=False),
        sa.Column("journal_transaction_id", sa.Text, sa.ForeignKey("journal_transactions.id")),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_balance_transactions_merchant_seq", "balance_transactions", ["merchant_id", "seq"]
    )
    op.execute(BALANCE_CHECK)
    # Platform accounts, seeded here (never created lazily).
    op.execute(
        "INSERT INTO ledger_accounts (id, merchant_id, kind, currency) VALUES "
        "('la_platform_clearing_usd', NULL, 'card_network_clearing', 'usd'), "
        "('la_platform_fees_usd', NULL, 'platform_fees', 'usd')"
    )
    # Backfill merchant accounts for merchants created before this migration.
    op.execute(
        "INSERT INTO ledger_accounts (id, merchant_id, kind, currency) "
        "SELECT 'la_' || substr(md5(m.id || k.kind), 1, 24), m.id, k.kind, 'usd' "
        "FROM merchants m CROSS JOIN (VALUES ('merchant_pending'), ('merchant_available')) "
        "AS k(kind)"
    )


def downgrade() -> None:
    op.drop_table("balance_transactions")
    op.execute("DROP TRIGGER IF EXISTS journal_entries_balanced ON journal_entries")
    op.drop_table("journal_entries")
    op.drop_table("journal_transactions")
    op.drop_table("ledger_accounts")
    op.execute("DROP FUNCTION IF EXISTS journal_entries_check()")
    op.execute("DROP FUNCTION IF EXISTS journal_append_only()")
