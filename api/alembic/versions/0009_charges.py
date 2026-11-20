"""charges

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "charges",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False, unique=True),
        sa.Column(
            "merchant_id",
            sa.Text,
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "payment_intent_id", sa.Text, sa.ForeignKey("payment_intents.id"), nullable=False
        ),
        sa.Column(
            "payment_method_id", sa.Text, sa.ForeignKey("payment_methods.id"), nullable=False
        ),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.Text, nullable=False),
        sa.Column("amount_captured", sa.BigInteger, nullable=False),
        sa.Column("amount_refunded", sa.BigInteger, nullable=False),
        sa.Column("captured", sa.Boolean, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("failure_code", sa.Text),
        sa.Column("failure_message", sa.Text),
        sa.Column("authorized_at", sa.DateTime(timezone=True)),
        sa.Column("balance_transaction_id", sa.Text, sa.ForeignKey("balance_transactions.id")),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('succeeded', 'failed')", name="charges_status"),
        sa.CheckConstraint(
            "amount_captured >= 0 AND amount_refunded >= 0 AND amount_refunded <= amount_captured",
            name="charges_amounts",
        ),
    )
    op.create_index("ix_charges_merchant_seq", "charges", ["merchant_id", "seq"])
    op.create_index("ix_charges_payment_intent_id", "charges", ["payment_intent_id"])
    op.create_index(
        "ux_charges_one_succeeded_per_pi",
        "charges",
        ["payment_intent_id"],
        unique=True,
        postgresql_where=sa.text("status = 'succeeded'"),
    )
    op.create_index(
        "ux_balance_transactions_one_capture",
        "balance_transactions",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("type = 'charge'"),
    )
    op.create_foreign_key(
        "fk_payment_intents_latest_charge",
        "payment_intents",
        "charges",
        ["latest_charge_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_payment_intents_latest_charge", "payment_intents", type_="foreignkey")
    op.drop_index("ux_balance_transactions_one_capture", table_name="balance_transactions")
    op.drop_table("charges")
