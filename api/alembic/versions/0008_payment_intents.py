"""payment_intents

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_intents",
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
        sa.Column("capture_method", sa.Text, nullable=False),
        sa.Column("amount_capturable", sa.BigInteger, nullable=False),
        sa.Column("amount_received", sa.BigInteger, nullable=False),
        sa.Column("customer_id", sa.Text, sa.ForeignKey("customers.id")),
        sa.Column("payment_method_id", sa.Text, sa.ForeignKey("payment_methods.id")),
        sa.Column("latest_charge_id", sa.Text),
        sa.Column("client_secret", sa.Text, nullable=False),
        sa.Column("return_url", sa.Text),
        sa.Column("last_payment_error", JSONB),
        sa.Column("next_action", JSONB),
        sa.Column("cancellation_reason", sa.Text),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('requires_payment_method', 'requires_confirmation', 'requires_action', "
            "'processing', 'requires_capture', 'canceled', 'succeeded')",
            name="payment_intents_status",
        ),
        sa.CheckConstraint(
            "capture_method IN ('automatic', 'automatic_async', 'manual')",
            name="payment_intents_capture_method",
        ),
    )
    op.create_index("ix_payment_intents_merchant_seq", "payment_intents", ["merchant_id", "seq"])


def downgrade() -> None:
    op.drop_table("payment_intents")
