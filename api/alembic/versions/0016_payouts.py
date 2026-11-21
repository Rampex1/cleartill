"""payouts: payouts table, payouts_in_transit platform account

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

OLD_KINDS = "'merchant_pending', 'merchant_available', 'card_network_clearing', 'platform_fees'"


def upgrade() -> None:
    op.drop_constraint("ledger_accounts_kind", "ledger_accounts", type_="check")
    op.create_check_constraint(
        "ledger_accounts_kind", "ledger_accounts", f"kind IN ({OLD_KINDS}, 'payouts_in_transit')"
    )
    op.execute(
        "INSERT INTO ledger_accounts (id, merchant_id, kind, currency) "
        "VALUES ('la_platform_payouts_usd', NULL, 'payouts_in_transit', 'usd')"
    )
    op.create_table(
        "payouts",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False, unique=True),
        sa.Column(
            "merchant_id",
            sa.Text,
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("arrival_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("balance_transaction_id", sa.Text, sa.ForeignKey("balance_transactions.id")),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("amount > 0", name="payouts_amount_positive"),
        sa.CheckConstraint("status IN ('pending', 'paid')", name="payouts_status"),
    )
    op.create_index("ix_payouts_merchant_seq", "payouts", ["merchant_id", "seq"])
    op.execute("CREATE INDEX payouts_due ON payouts (arrival_date) WHERE status = 'pending'")


def downgrade() -> None:
    op.drop_table("payouts")
    # Journal entries are append-only, so once a payout has posted, its account can't be
    # removed: keep the account and the wider kind constraint in that case (data first).
    op.execute(
        "DELETE FROM ledger_accounts WHERE id = 'la_platform_payouts_usd' AND NOT EXISTS "
        "(SELECT 1 FROM journal_entries WHERE account_id = 'la_platform_payouts_usd')"
    )
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM ledger_accounts WHERE kind = 'payouts_in_transit') THEN
            ALTER TABLE ledger_accounts DROP CONSTRAINT ledger_accounts_kind;
            ALTER TABLE ledger_accounts ADD CONSTRAINT ledger_accounts_kind
              CHECK (kind IN ({OLD_KINDS}));
          END IF;
        END $$
        """
    )
