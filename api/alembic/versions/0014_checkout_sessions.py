"""checkout_sessions

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "checkout_sessions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False, unique=True),
        sa.Column(
            "merchant_id",
            sa.Text,
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount_total", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.Text, nullable=False),
        sa.Column("line_items", JSONB, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("payment_status", sa.Text, nullable=False),
        sa.Column("payment_intent_id", sa.Text, sa.ForeignKey("payment_intents.id")),
        sa.Column("success_url", sa.Text, nullable=False),
        sa.Column("cancel_url", sa.Text),
        sa.Column("customer_id", sa.Text, sa.ForeignKey("customers.id")),
        sa.Column("client_reference_id", sa.Text),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('open', 'complete', 'expired')", name="checkout_sessions_status"
        ),
        sa.CheckConstraint(
            "payment_status IN ('unpaid', 'paid')", name="checkout_sessions_payment_status"
        ),
        sa.CheckConstraint("amount_total >= 50", name="checkout_sessions_amount_total"),
    )
    op.create_index(
        "ix_checkout_sessions_merchant_seq", "checkout_sessions", ["merchant_id", "seq"]
    )
    op.execute(
        "CREATE INDEX checkout_sessions_open_expiry ON checkout_sessions (expires_at) "
        "WHERE status = 'open'"
    )
    op.create_foreign_key(
        "fk_payment_intents_checkout_session",
        "payment_intents",
        "checkout_sessions",
        ["checkout_session_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_payment_intents_checkout_session", "payment_intents")
    op.drop_table("checkout_sessions")
