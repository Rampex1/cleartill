"""refunds

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refunds",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False, unique=True),
        sa.Column(
            "merchant_id",
            sa.Text,
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("charge_id", sa.Text, sa.ForeignKey("charges.id"), nullable=False),
        sa.Column(
            "payment_intent_id", sa.Text, sa.ForeignKey("payment_intents.id"), nullable=False
        ),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column(
            "balance_transaction_id",
            sa.Text,
            sa.ForeignKey("balance_transactions.id"),
            nullable=False,
        ),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="refunds_amount_positive"),
        sa.CheckConstraint("status IN ('succeeded')", name="refunds_status"),
    )
    op.create_index("ix_refunds_merchant_seq", "refunds", ["merchant_id", "seq"])
    op.create_index("ix_refunds_charge_id", "refunds", ["charge_id"])
    op.create_index("ix_refunds_payment_intent_id", "refunds", ["payment_intent_id"])
    op.create_index(
        "ux_balance_transactions_one_refund",
        "balance_transactions",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("type = 'refund'"),
    )


def downgrade() -> None:
    op.drop_index("ux_balance_transactions_one_refund", table_name="balance_transactions")
    op.drop_table("refunds")
